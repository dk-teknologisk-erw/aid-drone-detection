from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            Node(package="aid_system", executable="mcu_bridge", name="aid_mcu_bridge", output="screen"),
            Node(package="aid_system", executable="compass", name="aid_compass", output="screen"),
            Node(package="aid_system", executable="rf_sweep", name="aid_rf_sweep", output="screen"),
            Node(package="aid_system", executable="detector", name="aid_detector", output="screen"),
            Node(package="aid_system", executable="system_control", name="aid_system_control", output="screen"),
        ]
    )