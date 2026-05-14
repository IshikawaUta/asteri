from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="asteri",
    version="1.0.1",
    author="Ishikawa Uta",
    description="Asteri: High Performance Python Web Server",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/IshikawaUta/asteri",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Topic :: Internet :: WWW/HTTP :: WSGI :: Server",
        "Topic :: Internet :: WWW/HTTP :: WSGI :: Application",
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Environment :: Console",
    ],
    python_requires=">=3.8",
    install_requires=[
        "setproctitle>=1.3.3",
        "watchdog>=3.0.0",
        "h2>=4.1.0",
        "gevent>=23.9.1",
        "psutil>=5.9.0",
    ],
    entry_points={
        "console_scripts": [
            "asteri=asteri.__main__:main",
        ],
    },
)
