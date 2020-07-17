import pathlib
from setuptools import setup

# The directory containing this file
HERE = pathlib.Path(__file__).parent

# The text of the README file
README = (HERE / "README.md").read_text()

exec(open("zest/version.py").read())

setup(
    name="zbs.zest",
    version=__version__,
    description="A function-oriented testing framework for Python 3.",
    long_description=README,
    long_description_content_type="text/markdown",
    url="https://github.com/zsimpson/zbs.zest",
    author="Zack Booth Simpson",
    author_email="zack.simpson+pypi@gmail.com",
    license="GPLv3",
    classifiers=[
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Topic :: Software Development",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
    ],
    packages=["zest"],
    include_package_data=True,
    install_requires=[],
    python_requires=">=3.6",
)
