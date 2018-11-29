from setuptools import setup, find_packages

import acados_template

setup(name='acados_template',
   version='0.1',
   description='a templating framework for acados',
   url='http://github.com/zanellia/acados',
   author='Andrea Zanelli',
   license='LGPL',
   packages = find_packages(),
   zip_safe=False)
