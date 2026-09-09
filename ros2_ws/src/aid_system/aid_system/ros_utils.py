import rclpy
from rclpy.executors import ExternalShutdownException


def run_node(node_type, args=None) -> None:
    rclpy.init(args=args)
    node = node_type()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()