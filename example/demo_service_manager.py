# Copyright 2014 Zuercher Hochschule fuer Angewandte Wissenschaften
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from mcn.sm.service import Service

__author__ = 'andy'

from occi.core_model import Kind as Type
from occi.core_model import Resource

from mcn.sm.service import MCNApplication

if __name__ == '__main__':

    # defines the service to offer - the service owner defines this
    demo_svc_type = Type('http://schemas.mobile-cloud-networking.eu/occi/sm#',
                  'demo',
                  title='This is an example service type',
                  attributes={'hurtle.awesomeness.value':    'immutable'
                              },
                  related=[Resource.kind],
                  actions=[])

    # Create a service
    srv = Service(MCNApplication(), demo_svc_type)

    # Run the service manager
    srv.run()
