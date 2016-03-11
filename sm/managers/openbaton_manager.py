# Copyright 2014-2015 Zuercher Hochschule fuer Angewandte Wissenschaften
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


from sm.managers.generic import Task
from sm.config import CONFIG
from sm.log import LOG
from sm.retry_http import http_retriable_request
import time
import json

__author__ = 'merne'

obapi_addr = CONFIG.get('openbaton', 'host', None)
obapi_port = CONFIG.get('openbaton', 'port', None)
HTTP = 'http://' + obapi_addr + ':' + obapi_port

class Init(Task):

    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='initialise')

    def run(self):
        self.start_time = time.time()
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'init',
                    'phase_event': 'start',
                    'response_time': 0,
                    }
        LOG.debug(json.dumps(infoDict))
        self.entity.attributes['mcn.service.state'] = 'initialise'

        # Do init work here
        self.entity.extras = {}
        self.entity.extras['loc'] = 'foobar'
        self.entity.extras['tenant_name'] = self.extras['tenant_name']


        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'init',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    }
        LOG.debug(json.dumps(infoDict))
        return self.entity, self.extras


class Activate(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='activate')

    def run(self):
        self.start_time = time.time()
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'activate',
                    'phase_event': 'start',
                    'response_time': 0,
                    }
        LOG.debug(json.dumps(infoDict))
        self.entity.attributes['mcn.service.state'] = 'activate'

        # Do activate work here
        LOG.debug('Activating the SO...')
        url = HTTP + '/api/v1/occi/default'
        heads = {
            'Category': 'orchestrator; scheme="http://schemas.mobile-cloud-networking.eu/occi/service#"',
            'Content-Type': 'text/occi',
            'X-Auth-Token': self.extras['token'],
            'X-Tenant-Name': self.extras['tenant_name'],
        }

        occi_attrs = self.extras['srv_prms'].service_parameters(self.state)
        if len(occi_attrs) > 0:
            LOG.info('Adding service-specific parameters to call... X-OCCI-Attribute: ' + occi_attrs)
            heads['X-OCCI-Attribute'] = occi_attrs

        LOG.debug('Initialising SO with: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())
        http_retriable_request('PUT', url, headers=heads)

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'activate',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    }
        LOG.debug(json.dumps(infoDict))
        return self.entity, self.extras


class Deploy(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='deploy')

    def run(self):
        self.start_time = time.time()
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'deploy',
                    'phase_event': 'start',
                    'response_time': 0,
                    }
        LOG.debug(json.dumps(infoDict))
        self.entity.attributes['mcn.service.state'] = 'deploy'

        # Do deploy work here
        url = HTTP + '/api/v1/occi/default'
        params = {'action': 'deploy'}
        heads = {
            'Category': 'deploy; scheme="http://schemas.mobile-cloud-networking.eu/occi/service#"',
            'Content-Type': 'text/occi',
            'X-Auth-Token': self.extras['token'],
            'X-Tenant-Name': self.extras['tenant_name']}
        occi_attrs = self.extras['srv_prms'].service_parameters(self.state)
        if len(occi_attrs) > 0:
            LOG.info('Adding service-specific parameters to call... X-OCCI-Attribute:' + occi_attrs)
            heads['X-OCCI-Attribute'] = occi_attrs
        LOG.debug('Deploying SO with: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())
        http_retriable_request('POST', url, headers=heads, params=params)

        # also sleep here to keep phases consistent during greenfield
        while not self.deploy_complete(url):
                    time.sleep(7)

        self.entity.attributes['mcn.service.state'] = 'deploy'
        LOG.debug('SO Deployed ')

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'deploy',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    }
        LOG.debug(json.dumps(infoDict))
        return self.entity, self.extras

    def deploy_complete(self, url):
        heads = {
            'Content-type': 'text/occi',
            'Accept': 'application/occi+json',
            'X-Auth-Token': self.extras['token'],
            'X-Tenant-Name': self.extras['tenant_name'],
        }

        LOG.info('checking service state at: ' + url)
        LOG.info('sending headers: ' + heads.__repr__())

        r = http_retriable_request('GET', url, headers=heads)
        rheaders = r.headers.get("x-occi-attribute").split(',')

        for entry in rheaders:
            if 'occi.stack.state' in entry:
                stack_state = entry.split('"')[1]
                if 'CREATE_COMPLETE' in entry or 'UPDATE_COMPLETE' in entry:
                    LOG.info('Stack is ready')
                    return True
                else:
                    LOG.info('Stack is not ready. Current state state: ' + stack_state)
                    return False


class Provision(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='provision')

    def run(self):
        self.start_time = time.time()
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'provision',
                    'phase_event': 'start',
                    'response_time': 0,
                    }
        LOG.debug(json.dumps(infoDict))
        self.entity.attributes['mcn.service.state'] = 'provision'

        # Do provision work here

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'provision',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    }
        LOG.debug(json.dumps(infoDict))
        return self.entity, self.extras


class Retrieve(Task):

    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, 'retrieve')

    def run(self):
        self.start_time = time.time()
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'retrieve',
                    'phase_event': 'start',
                    'response_time': 0,
                    }
        LOG.debug(json.dumps(infoDict))

        # Do retrieve work here
        if self.entity.attributes['mcn.service.state'] in ['activate',
                                                           'deploy',
                                                           'provision',
                                                           'update']:
            heads = {
                'Content-Type': 'text/occi',
                'Accept': 'text/occi',
                'X-Auth-Token': self.extras['token'],
                'X-Tenant-Name': self.extras['tenant_name']}
            LOG.info('Getting state of service orchestrator with: ' +
                     "localhost:8082" + '/api/v1/occidefault')
            LOG.info('Sending headers: ' + heads.__repr__())
            r = http_retriable_request('GET', HTTP +
                                       '/api/v1/occi/default', headers=heads)

            attrs = r.headers['x-occi-attribute'].split(', ')
            for attr in attrs:
                kv = attr.split('=')
                if kv[0] != 'occi.core.id':
                    if kv[1].startswith('"') and kv[1].endswith('"'):
                        kv[1] = kv[1][1:-1]  # scrub off quotes
                    self.entity.attributes[kv[0]] = kv[1]
                    LOG.debug('OCCI Attribute: ' + kv[0] + ' --> ' + kv[1])

        else:
            LOG.debug('Cannot GET entity as it is not in the activated, '
                      'deployed or provisioned, updated state')

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'retrieve',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    }
        LOG.debug(json.dumps(infoDict))
        return self.entity, self.extras


# can only be executed when provisioning is complete
class Update(Task):
    def __init__(self, entity, extras, updated_entity):
        Task.__init__(self, entity, extras, state='update')
        self.new = updated_entity

    def run(self):
        self.start_time = time.time()
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'update',
                    'phase_event': 'start',
                    'response_time': 0,
                    }
        LOG.debug(json.dumps(infoDict))
        self.entity.attributes['mcn.service.state'] = 'update'

        # Do update work here

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'update',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    }
        LOG.debug(json.dumps(infoDict))
        return self.entity, self.extras


class Destroy(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='destroy')

    def run(self):
        self.start_time = time.time()
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'destroy',
                    'phase_event': 'start',
                    'response_time': 0,
                    }
        LOG.debug(json.dumps(infoDict))
        self.entity.attributes['mcn.service.state'] = 'destroy'

        # Do destroy work here
        url = HTTP + '/api/v1/occi/default'
        heads = {'X-Auth-Token': self.extras['token'],
                 'X-Tenant-Name': self.extras['tenant_name']}
        occi_attrs = self.extras['srv_prms'].service_parameters(self.state)
        if len(occi_attrs) > 0:
            LOG.info('Adding service-specific parameters to call... '
                     'X-OCCI-Attribute:' + occi_attrs)
            heads['X-OCCI-Attribute'] = occi_attrs
        LOG.info('Disposing service orchestrator with: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())

        http_retriable_request('DELETE', url, headers=heads)

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'sm_name': self.entity.kind.term,
                    'phase': 'destroy',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    }
        LOG.debug(json.dumps(infoDict))
        return self.entity, self.extras
