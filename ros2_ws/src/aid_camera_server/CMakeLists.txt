cmake_minimum_required(VERSION 3.16)
project(aid_camera_server)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(camera_info_manager REQUIRED)
find_package(image_transport REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

set(PYLON_ROOT "/opt/pylon" CACHE PATH "Basler pylon installation")
find_path(PYLON_INCLUDE_DIR pylon/PylonIncludes.h
  PATHS "${PYLON_ROOT}/include"
  NO_DEFAULT_PATH)
find_library(PYLON_BASE_LIBRARY pylonbase
  PATHS "${PYLON_ROOT}/lib"
  NO_DEFAULT_PATH)
find_library(PYLON_UTILITY_LIBRARY pylonutility
  PATHS "${PYLON_ROOT}/lib"
  NO_DEFAULT_PATH)
find_library(PYLON_GCBASE_LIBRARY GCBase_gcc_v3_5_Basler_pylon_v1
  PATHS "${PYLON_ROOT}/lib"
  NO_DEFAULT_PATH)
find_library(PYLON_GENAPI_LIBRARY GenApi_gcc_v3_5_Basler_pylon_v1
  PATHS "${PYLON_ROOT}/lib"
  NO_DEFAULT_PATH)

if(NOT PYLON_INCLUDE_DIR OR NOT PYLON_BASE_LIBRARY OR NOT PYLON_UTILITY_LIBRARY
   OR NOT PYLON_GCBASE_LIBRARY OR NOT PYLON_GENAPI_LIBRARY)
  message(FATAL_ERROR "Basler pylon SDK not found under ${PYLON_ROOT}")
endif()

add_executable(aid_camera_server_node src/aid_camera_server_node.cpp)
target_include_directories(aid_camera_server_node PRIVATE "${PYLON_INCLUDE_DIR}")
target_link_libraries(aid_camera_server_node
  "${PYLON_BASE_LIBRARY}"
  "${PYLON_UTILITY_LIBRARY}"
  "${PYLON_GCBASE_LIBRARY}"
  "${PYLON_GENAPI_LIBRARY}")
ament_target_dependencies(aid_camera_server_node
  camera_info_manager
  image_transport
  rclcpp
  sensor_msgs)

install(TARGETS aid_camera_server_node
  DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY config launch
  DESTINATION share/${PROJECT_NAME})

ament_package()