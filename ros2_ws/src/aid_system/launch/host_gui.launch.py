from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            Node(package="aid_system", executable="operator_gui", name="aid_operator_gui", output="screen"),
        ]
    )