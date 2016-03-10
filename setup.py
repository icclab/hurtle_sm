#!/usr/bin/env python

# Copyright 2014-2015 Zuercher Hochschule fuer Angewandte Wissenschaften
#   All Rights Reserved.
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from setuptools import setup

setup(
    name='sm',
    version='0.4',
    description='Command and library for creating service managers and orchestrators',
    author='Andy Edmonds',
    author_email='edmo@zhaw.ch',
    url='http://blog.zhaw.ch/icclab',
    license='Apache 2.0',
    packages=['sm', 'sm.so', 'sm.managers'],
    install_requires=[
        'pyssf',
        'requests',
        'mako',
        'retrying',
        'tornado',
        'graypy',
        'debtcollector',
        'monotonic',
        'pycrypto',
        'ecdsa',
        'jsonpickle',
        'python-keystoneclient',
        'pymongo',
	'flask'],
    scripts=['bin/service_manager']
)
