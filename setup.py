# USAGE NOTES
#
# Make a PyPI release tarball with:
#
#     python setup.py sdist
#
# Upload to test PyPI with:
#
#     twine upload --repository-url https://test.pypi.org/legacy/ dist/*
#
# Install from test PyPI with:
#
#     pip install --index-url https://test.pypi.org/simple/ lyse
#
# Upload to real PyPI with:
#
#     twine upload dist/*
#
# Build conda packages for all platforms (in a conda environment with setuptools_conda
# installed) with:
#
#     python setup.py dist_conda
#
# Upoad to your own account (for testing) on anaconda cloud (in a conda environment with
# anaconda-client installed) with:
#
#     anaconda upload --skip-existing conda_packages/*/*
#
# (Trickier on Windows, as it won't expand the wildcards)
#
# Upoad to the labscript-suite organisation's channel on anaconda cloud (in a
# conda environment with anaconda-client installed) with:
#
#     anaconda -c labscript-suite upload --skip-existing conda_packages/*/*
#
# If you need to rebuild the same version of the package for conda due to a packaging
# issue, you must increment CONDA_BUILD_NUMBER in order to create a unique version on
# anaconda cloud. When subsequently releasing a new version of the package,
# CONDA_BUILD_NUMBER should be reset to zero.

import os
from setuptools import setup
from runpy import run_path

try:
    from setuptools_conda import dist_conda
except ImportError:
    dist_conda = None

INSTALL_REQUIRES = [
    "labscript_utils >=2.14.0",
    "runmanager",
    "qtutils >=2.2.2",
    "zprocess >=2.2.2",
    "pandas >=0.21",
    "h5py",
    "numpy",
    "matplotlib",
    "scipy",
    "tzlocal",
]

setup(
    name='lyse',
    version=run_path(os.path.join('lyse', '__version__.py'))['__version__'],
    description="Program to set parameters and compile shots with labscript",
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    author='The labscript suite community',
    author_email='labscriptsuite@googlegroups.com ',
    url='http://labscriptsuite.org',
    license="BSD",
    packages=["lyse"],
    zip_safe=False,
    setup_requires=['setuptools', 'setuptools_scm'],
    include_package_data=True,
    python_requires=">=3.6",
    install_requires=INSTALL_REQUIRES if 'CONDA_BUILD' not in os.environ else [],
    cmdclass={'dist_conda': dist_conda} if dist_conda is not None else {},
    command_options={
        'dist_conda': {
            'pythons': (__file__, ['3.6', '3.7', '3.8']),
            'platforms': (__file__, ['linux-64', 'win-32', 'win-64', 'osx-64']),
            'force_conversion': (__file__, True),
        },
    },
)
