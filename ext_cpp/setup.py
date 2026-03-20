from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

ext_modules = [
    Pybind11Extension("entropy_c", ["entropy.cpp"]),
]

setup(
    name="entropy_c",
    version="0.1.0",
    description="High-performance Shannon Entropy C++ Extension",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
