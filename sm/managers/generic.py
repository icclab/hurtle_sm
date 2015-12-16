# Copyright 2014-2015 Zuercher Hochschule fuer Angewandte Wissenschaften
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
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

__author__ = 'merne'

from sm.log import LOG
import json
from sm.config import CONFIG
from threading import Thread



class ServiceParameters:
    def __init__(self):
        self.service_params = {}
        service_params_file_path = CONFIG.get('service_manager', 'service_params', '')
        if len(service_params_file_path) > 0:
            try:
                with open(service_params_file_path) as svc_params_content:
                    self.service_params = json.load(svc_params_content)
                    svc_params_content.close()
            except ValueError:  # as e:
                LOG.error("Invalid JSON sent as service config file")
            except IOError:  # as e:
                LOG.error('Cannot find the specified parameters file: ' + service_params_file_path)
        else:
            LOG.warn("No service parameters file found in config file, setting internal params to empty.")

    def service_parameters(self, state='', content_type='text/occi'):
        # takes the internal parameters defined for the lifecycle phase...
        #       and combines them with the client supplied parameters
        if content_type == 'text/occi':
            params = []
            # get the state specific internal parameters
            try:
                params = self.service_params[state]
            except KeyError:  # as err:
                LOG.warn('The requested states parameters are not available: "' + state + '"')

            # get the client supplied parameters if any
            try:
                for param in self.service_params['client_params']:
                    params.append(param)
            except KeyError:  # as err:
                LOG.info('No client params')

            header = ''
            for param in params:
                if param['type'] == 'string':
                    value = '"' + param['value'] + '"'
                else:
                    value = str(param['value'])

                header = header + param['name'] + '=' + value + ', '

            return header[0:-2]
        else:
            LOG.error('Content type not supported: ' + content_type)

    def add_client_params(self, params={}):
        # adds user supplied parameters from the instantiation request of a service
        client_params = []

        for k, v in params.items():
            param_type = 'number'
            if (v.startswith('"') or v.startswith('\'')) and (v.endswith('"') or v.endswith('\'')):
                param_type = 'string'
                v = v[1:-1]
            param = {'name': k, 'value': v, 'type': param_type}

            client_params.append(param)

        self.service_params['client_params'] = client_params


if __name__ == '__main__':
    sp = ServiceParameters()
    cp = {
        'test': '1',
        'test.test': '"astring"'
    }
    sp.add_client_params(cp)

    p = sp.service_parameters('initialise')
    print p


class AsychExe(Thread):
    """
    Only purpose of this thread is to execute a list of tasks sequentially
    as a background "thread".
    """
    def __init__(self, tasks, registry=None):
        super(AsychExe, self).__init__()
        self.registry = registry
        self.tasks = tasks

    def run(self):
        super(AsychExe, self).run()
        LOG.debug('Starting AsychExe thread')

        for task in self.tasks:
            entity, extras = task.run()
            if self.registry:
                LOG.debug('Updating entity in registry')
                self.registry.add_resource(key=entity.identifier, resource=entity, extras=extras)

class Task:

    def __init__(self, entity, extras, state):
        self.entity = entity
        self.extras = extras
        self.state = state
        self.start_time = ''

    def run(self):
        raise NotImplemented()