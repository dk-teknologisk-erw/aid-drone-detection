#include <pylon/PylonIncludes.h>
#include <cinttypes>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#include <camera_info_manager/camera_info_manager.hpp>
#include <image_transport/image_transport.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/image_encodings.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <sensor_msgs/msg/image.hpp>

using namespace std::chrono_literals;

namespace aid_camera_server
{

class PylonRuntime
{
public:
  PylonRuntime() {Pylon::PylonInitialize();}
  ~PylonRuntime() {Pylon::PylonTerminate();}
};

class AidCameraServer : public rclcpp::Node
{
public:
  AidCameraServer()
  : Node("aid_camera_server"),
    camera_info_manager_(this, "aid_camera")
  {
    serial_number_ = declare_parameter<std::string>("serial_number", "");
    frame_id_ = declare_parameter<std::string>("frame_id", "camera_optical_frame");
    camera_info_url_ = declare_parameter<std::string>("camera_info_url", "");
    frame_rate_hz_ = declare_parameter<double>("frame_rate_hz", 10.0);
    exposure_auto_ = declare_parameter<bool>("exposure_auto", true);
    exposure_time_us_ = declare_parameter<double>("exposure_time_us", 3000.0);
    gain_auto_ = declare_parameter<bool>("gain_auto", true);
    gain_db_ = declare_parameter<double>("gain_db", 0.0);
    grab_timeout_ms_ = declare_parameter<int>("grab_timeout_ms", 2000);
    reconnect_delay_ms_ = declare_parameter<int>("reconnect_delay_ms", 1000);

    if (frame_rate_hz_ <= 0.0 || grab_timeout_ms_ <= 0 || reconnect_delay_ms_ <= 0) {
      throw std::invalid_argument("frame_rate_hz and timeout parameters must be positive");
    }
    if (!camera_info_url_.empty() && !camera_info_manager_.loadCameraInfo(camera_info_url_)) {
      throw std::runtime_error("failed to load camera_info_url: " + camera_info_url_);
    }

    camera_publisher_ = image_transport::create_camera_publisher(
      this, "image_raw", rclcpp::SensorDataQoS().get_rmw_qos_profile());

    running_.store(true);
    capture_thread_ = std::thread(&AidCameraServer::captureLoop, this);
  }

  ~AidCameraServer() override
  {
    running_.store(false);
    if (capture_thread_.joinable()) {
      capture_thread_.join();
    }
    closeCamera();
  }

private:
  void setEnum(GenApi::INodeMap & node_map, const char * name, const char * value)
  {
    GenApi::CEnumerationPtr parameter = node_map.GetNode(name);
    if (!parameter || !GenApi::IsWritable(parameter)) {
      return;
    }
    GenApi::CEnumEntryPtr entry = parameter->GetEntryByName(value);
    if (entry && GenApi::IsAvailable(entry)) {
      parameter->SetIntValue(entry->GetValue());
    }
  }

  void setFloat(GenApi::INodeMap & node_map, const char * name, double value)
  {
    GenApi::CFloatPtr parameter = node_map.GetNode(name);
    if (!parameter || !GenApi::IsWritable(parameter)) {
      return;
    }
    const double bounded = std::clamp(value, parameter->GetMin(), parameter->GetMax());
    parameter->SetValue(bounded);
  }

  void connectCamera()
  {
    Pylon::CTlFactory & factory = Pylon::CTlFactory::GetInstance();
    Pylon::DeviceInfoList_t devices;
    factory.EnumerateDevices(devices);
    if (devices.empty()) {
      throw std::runtime_error("no Basler camera found");
    }

    auto selected = devices.begin();
    if (!serial_number_.empty()) {
      selected = std::find_if(
        devices.begin(), devices.end(), [this](const Pylon::CDeviceInfo & device) {
          return std::string(device.GetSerialNumber().c_str()) == serial_number_;
        });
      if (selected == devices.end()) {
        throw std::runtime_error("configured camera serial not found: " + serial_number_);
      }
    } else if (devices.size() != 1U) {
      throw std::runtime_error("multiple cameras found; set serial_number");
    }

    camera_ = std::make_unique<Pylon::CInstantCamera>(factory.CreateDevice(*selected));
    camera_->Open();
    serial_number_ = camera_->GetDeviceInfo().GetSerialNumber().c_str();

    GenApi::INodeMap & node_map = camera_->GetNodeMap();
    setEnum(node_map, "TriggerSelector", "FrameStart");
    setEnum(node_map, "TriggerMode", "Off");
    setEnum(node_map, "AcquisitionMode", "Continuous");
    setEnum(node_map, "ExposureAuto", exposure_auto_ ? "Continuous" : "Off");
    if (exposure_auto_) {
      // Cap auto exposure to the frame period so it cannot lower the frame rate.
      const double frame_period_us = 1e6 / frame_rate_hz_;
      setFloat(node_map, "AutoExposureTimeUpperLimit", frame_period_us);
      setFloat(node_map, "AutoExposureTimeAbsUpperLimit", frame_period_us);
    } else {
      setFloat(node_map, "ExposureTime", exposure_time_us_);
    }
    setEnum(node_map, "GainAuto", gain_auto_ ? "Continuous" : "Off");
    if (!gain_auto_) {
      setFloat(node_map, "Gain", gain_db_);
    }

    GenApi::CBooleanPtr frame_rate_enable = node_map.GetNode("AcquisitionFrameRateEnable");
    if (frame_rate_enable && GenApi::IsWritable(frame_rate_enable)) {
      frame_rate_enable->SetValue(true);
    }
    setFloat(node_map, "AcquisitionFrameRate", frame_rate_hz_);

    converter_.OutputPixelFormat = Pylon::PixelType_RGB8packed;
    converter_.OutputBitAlignment = Pylon::OutputBitAlignment_MsbAligned;
    camera_->StartGrabbing(Pylon::GrabStrategy_LatestImageOnly);

    RCLCPP_INFO(
      get_logger(), "Connected %s serial=%s at %.2f Hz",
      camera_->GetDeviceInfo().GetModelName().c_str(), serial_number_.c_str(), frame_rate_hz_);
  }

  void closeCamera()
  {
    if (!camera_) {
      return;
    }
    try {
      if (camera_->IsGrabbing()) {
        camera_->StopGrabbing();
      }
      if (camera_->IsOpen()) {
        camera_->Close();
      }
    } catch (const Pylon::GenericException & error) {
      RCLCPP_WARN(get_logger(), "Error closing camera: %s", error.GetDescription());
    }
    camera_.reset();
  }

  void publishFrame(const Pylon::CGrabResultPtr & result)
  {
    Pylon::CPylonImage converted;
    converter_.Convert(converted, result);

    sensor_msgs::msg::Image image;
    image.header.stamp = now();
    image.header.frame_id = frame_id_;
    image.height = static_cast<uint32_t>(converted.GetHeight());
    image.width = static_cast<uint32_t>(converted.GetWidth());
    image.encoding = sensor_msgs::image_encodings::RGB8;
    image.is_bigendian = false;
    image.step = image.width * 3U;
    image.data.resize(static_cast<std::size_t>(image.step) * image.height);
    std::memcpy(image.data.data(), converted.GetBuffer(), image.data.size());

    sensor_msgs::msg::CameraInfo camera_info = camera_info_manager_.getCameraInfo();
    camera_info.header = image.header;
    camera_info.height = image.height;
    camera_info.width = image.width;

    camera_publisher_.publish(image, camera_info);
    ++published_frames_;

    if (published_frames_ % 100U == 0U) {
      RCLCPP_INFO(
        get_logger(), "Published %" PRIu64 " frames", published_frames_.load());
    }
  }

  void captureLoop()
  {
    while (rclcpp::ok() && running_.load()) {
      try {
        if (!camera_) {
          connectCamera();
        }

        Pylon::CGrabResultPtr result;
        camera_->RetrieveResult(
          static_cast<unsigned int>(grab_timeout_ms_), result,
          Pylon::TimeoutHandling_ThrowException);
        if (!result || !result->GrabSucceeded()) {
          throw std::runtime_error(
                  result ? result->GetErrorDescription().c_str() : "empty grab result");
        }
        publishFrame(result);
      } catch (const Pylon::GenericException & error) {
        ++capture_errors_;
        RCLCPP_ERROR_THROTTLE(
          get_logger(), *get_clock(), 5000,
          "Pylon error (%" PRIu64 " total): %s; reconnecting",
          capture_errors_.load(), error.GetDescription());
        closeCamera();
        std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_delay_ms_));
      } catch (const std::exception & error) {
        ++capture_errors_;
        RCLCPP_ERROR_THROTTLE(
          get_logger(), *get_clock(), 5000,
          "Camera error (%" PRIu64 " total): %s; reconnecting",
          capture_errors_.load(), error.what());
        closeCamera();
        std::this_thread::sleep_for(std::chrono::milliseconds(reconnect_delay_ms_));
      }
    }
  }

  PylonRuntime pylon_runtime_;
  camera_info_manager::CameraInfoManager camera_info_manager_;
  image_transport::CameraPublisher camera_publisher_;
  std::unique_ptr<Pylon::CInstantCamera> camera_;
  Pylon::CImageFormatConverter converter_;
  std::thread capture_thread_;
  std::atomic<bool> running_{false};
  std::atomic<uint64_t> published_frames_{0};
  std::atomic<uint64_t> capture_errors_{0};
  std::string serial_number_;
  std::string frame_id_;
  std::string camera_info_url_;
  double frame_rate_hz_;
  bool exposure_auto_;
  double exposure_time_us_;
  bool gain_auto_;
  double gain_db_;
  int grab_timeout_ms_;
  int reconnect_delay_ms_;
};

}  // namespace aid_camera_server

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<aid_camera_server::AidCameraServer>());
  } catch (const std::exception & error) {
    std::cerr << "aid_camera_server failed: " << error.what() << std::endl;
    rclcpp::shutdown();
    return 1;
  }
  rclcpp::shutdown();
  return 0;
}
