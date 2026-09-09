from glob import glob
import os

from setuptools import setup


package_name = "aid_system"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="erw",
    maintainer_email="erw@teknologisk.dk",
    description="Directional RF detection nodes and operator GUI",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "compass = aid_system.compass_node:main",
            "detector = aid_system.detector_node:main",
            "mcu_bridge = aid_system.mcu_bridge_node:main",
            "operator_gui = aid_system.operator_gui:main",
            "rf_sweep = aid_system.rf_sweep_node:main",
            "system_control = aid_system.control_node:main",
        ],
    },
)