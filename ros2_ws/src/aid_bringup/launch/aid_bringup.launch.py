import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import (
    AnyLaunchDescriptionSource,
    PythonLaunchDescriptionSource,
)


def generate_launch_description() -> LaunchDescription:
    camera_launch = os.path.join(
        get_package_share_directory("aid_camera_server"),
        "launch",
        "aid_camera_server.launch.yaml",
    )
    system_launch = os.path.join(
        get_package_share_directory("aid_system"),
        "launch",
        "pi_system.launch.py",
    )

    return LaunchDescription(
        [
            SetEnvironmentVariable("ROS_DOMAIN_ID", "0"),
            SetEnvironmentVariable("ROS_LOCALHOST_ONLY", "1"),
            SetEnvironmentVariable("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST"),
            IncludeLaunchDescription(AnyLaunchDescriptionSource(camera_launch)),
            IncludeLaunchDescription(PythonLaunchDescriptionSource(system_launch)),
        ]
    )