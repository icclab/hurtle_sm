# Copyright 2014-2015 Zuercher Hochschule fuer Angewandte Wissenschaften
# Copyright (c) 2013-2015, Intel Performance Learning Solutions Ltd, Intel Corporation.
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

from distutils import dir_util
import json
from mako.template import Template  # XXX unneeded
import os
import random
import shutil
import tempfile
import time
from threading import Thread
from urlparse import urlparse
import uuid

from occi.core_model import Resource, Link
from sm.config import CONFIG
from sm.log import LOG
from sm.retry_http import http_retriable_request
from sm.managers.generic import Task


__author__ = 'andy'

HTTP = 'http://'
WAIT = int(CONFIG.get('cloud_controller', 'wait_time', 2000))
ATTEMPTS = int(CONFIG.get('cloud_controller', 'max_attempts', 5))


# instantiate container
class InitSO(Task):

    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='initialise')
        self.nburl = os.environ.get('CC_URL', False)
        if not self.nburl:
            self.nburl = CONFIG.get('cloud_controller', 'nb_api', '')
        if self.nburl[-1] == '/':
            self.nburl = self.nburl[0:-1]
        LOG.info('CloudController Northbound API: ' + self.nburl)
        if len(entity.attributes) > 0:
            LOG.info('Client supplied parameters: ' + entity.attributes.__repr__())
            # XXX check that these parameters are valid according to the kind specification
            self.extras['srv_prms'].add_client_params(entity.attributes)
        else:
            LOG.warn('No client supplied parameters.')

    def run(self):
        #LOG.debug('INIT SO START')
        self.start_time = time.time()
        self.extras['occi.init.starttime'] = self.start_time
        if not self.entity.extras:
            self.entity.extras = {}
        ops_version = self.__detect_ops_version()
        self.entity.extras['ops_version'] = ops_version

        self.entity.attributes['mcn.service.state'] = 'initialise'

        # create an app for the new SO instance
        LOG.debug('Creating SO container...')
        self.__create_app()

        # adding tenant to entity.extras for future checks later when retrieving resource
        self.entity.extras['tenant_name'] = self.extras['tenant_name']
        return self.entity, self.extras

    def __detect_ops_version(self):
        # make a call to the cloud controller and based on the app kind, heuristically select version
        version = 'v2'
        heads = {
            'Content-Type': 'text/occi',
            'Accept': 'text/occi'
            }
        url = self.nburl + '/-/'
        LOG.debug('Requesting CC Query Interface: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())
        r = http_retriable_request('GET', url, headers=heads, authenticate=True)
        if r.headers['category'].find('occi.app.image') > -1 and r.headers['category'].find('occi.app.env') > -1:
            LOG.info('Found occi.app.image and occi.app.env - this is OpenShift V3')
            version = 'v3'
        else:
            LOG.info('This is OpenShift V2')
        return version

    def __create_app(self):

        # will generate an appname 24 chars long - compatible with v2 and v3
        # e.g. soandycd009b39c28790f3
        app_name = 'so' + self.entity.kind.term[0:4] + \
                   ''.join(random.choice('0123456789abcdef') for _ in range(16))
        heads = {'Content-Type': 'text/occi'}

        url = self.nburl + '/app/'

        if self.entity.extras['ops_version'] == 'v2':
            heads['category'] = 'app; scheme="http://schemas.ogf.org/occi/platform#", ' \
                                'python-2.7; scheme="http://schemas.openshift.com/template/app#", ' \
                                'small; scheme="http://schemas.openshift.com/template/app#"'
            heads['X-OCCI-Attribute'] = str('occi.app.name=' + app_name)
            LOG.debug('Ensuring SM SSH Key...')
            self.__ensure_ssh_key()
        elif self.entity.extras['ops_version'] == 'v3':

            # for OpSv3 bundle location is the repo id of the container image
            bundle_loc = os.environ.get('BUNDLE_LOC', False)
            if not bundle_loc:
                bundle_loc = CONFIG.get('service_manager', 'bundle_location', '')
            if bundle_loc == '':
                LOG.error('No bundle_location parameter supplied in sm.cfg')
                raise Exception('No bundle_location parameter supplied in sm.cfg')
            if bundle_loc.startswith('/'):
                LOG.warn('Bundle location does not look like an image reference!')
            LOG.debug('Bundle to execute: ' + bundle_loc)

            design_uri = CONFIG.get('service_manager', 'design_uri', '')
            if design_uri == '':
                raise Exception('No design_uri parameter supplied in sm.cfg')
            LOG.debug('Design URI: ' + design_uri)

            heads['category'] = 'app; scheme="http://schemas.ogf.org/occi/platform#"'
            # TODO provide a means to provide additional docker env params
            attrs = 'occi.app.name="' + app_name + '", ' + \
                    'occi.app.image="' + bundle_loc + '", ' + \
                    'occi.app.env="DESIGN_URI=' + design_uri + '"'
            heads['X-OCCI-Attribute'] = str(attrs)
        else:
            LOG.error('Unknown OpenShift version. ops_version: ' + self.entity.extras['ops_version'])
            raise Exception('Unknown OpenShift version. ops_version: ' + self.entity.extras['ops_version'])

        LOG.debug('Requesting container to execute SO Bundle: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())
        r = http_retriable_request('POST', url, headers=heads, authenticate=True)

        loc = r.headers.get('Location', '')
        if loc == '':
            LOG.error("No OCCI Location attribute found in request")
            raise AttributeError("No OCCI Location attribute found in request")

        self.entity.attributes['occi.so.url'] = loc

        app_uri_path = urlparse(loc).path
        LOG.debug('SO container created: ' + app_uri_path)

        LOG.debug('Updating OCCI entity.identifier from: ' + self.entity.identifier + ' to: ' +
                  app_uri_path.replace('/app/', self.entity.kind.location))
        self.entity.identifier = app_uri_path.replace('/app/', self.entity.kind.location)

        LOG.debug('Setting occi.core.id to: ' + app_uri_path.replace('/app/', ''))
        self.entity.attributes['occi.core.id'] = app_uri_path.replace('/app/', '')

        # its a bit wrong to put this here, but we do not have the required information before.
        # this keeps things consistent as the timing is done right
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'].split('/'),
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'init',
                    'phase_event': 'start',
                    'response_time': 0,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        # OpSv2 only: get git uri. this is where our bundle is pushed to
        # XXX this is fugly
        # TODO use the same name for the app URI
        if self.entity.extras['ops_version'] == 'v2':
            self.entity.extras['repo_uri'] = self.__git_uri(app_uri_path)
        elif self.entity.extras['ops_version'] == 'v3':
            self.entity.extras['loc'] = self.__git_uri(app_uri_path)

    def __git_uri(self, app_uri_path):
        url = self.nburl + app_uri_path
        headers = {'Accept': 'text/occi'}
        LOG.debug('Requesting container\'s URL ' + url)
        LOG.info('Sending headers: ' + headers.__repr__())
        r = http_retriable_request('GET', url, headers=headers, authenticate=True)

        attrs = r.headers.get('X-OCCI-Attribute', '')
        if attrs == '':
            raise AttributeError("No occi attributes found in request")

        repo_uri = ''
        for attr in attrs.split(', '):
            if attr.find('occi.app.repo') != -1:
                repo_uri = attr.split('=')[1][1:-1]  # scrubs trailing wrapped quotes
                break
            elif attr.find('occi.app.url') != -1:
                repo_uri = attr.split('=')[1][1:-1]  # scrubs trailing wrapped quotes
                break
        if repo_uri == '':
            raise AttributeError("No occi.app.repo or occi.app.url attribute found in request")

        LOG.debug('SO container URL: ' + repo_uri)

        return repo_uri

    def __ensure_ssh_key(self):
        url = self.nburl + '/public_key/'
        heads = {'Accept': 'text/occi'}
        resp = http_retriable_request('GET', url, headers=heads, authenticate=True)
        locs = resp.headers.get('x-occi-location', '')
        # Split on spaces, test if there is at least one key registered
        if len(locs.split()) < 1:
            LOG.debug('No SM SSH registered. Registering default SM SSH key.')
            occi_key_name, occi_key_content = self.__extract_public_key()

            create_key_headers = {'Content-Type': 'text/occi',
                                  'Category': 'public_key; scheme="http://schemas.ogf.org/occi/security/credentials#"',
                                  'X-OCCI-Attribute': 'occi.key.name="' + occi_key_name + '", occi.key.content="' +
                                                      occi_key_content + '"'
                                  }
            http_retriable_request('POST', url, headers=create_key_headers, authenticate=True)
        else:
            LOG.debug('Valid SM SSH is registered with OpenShift.')

    def __extract_public_key(self):

        ssh_key_file = CONFIG.get('service_manager', 'ssh_key_location', '')
        if ssh_key_file == '':
            raise Exception('No ssh_key_location parameter supplied in sm.cfg')
        LOG.debug('Using SSH key file: ' + ssh_key_file)

        with open(ssh_key_file, 'r') as content_file:
            content = content_file.read()
            content = content.split()

            if content[0] == 'ssh-dsa':
                raise Exception("The supplied key is not a RSA ssh key. Location: " + ssh_key_file)

            key_content = content[1]
            key_name = 'servicemanager'

            if len(content) == 3:
                key_name = content[2]

            return key_name, key_content


# instantiate SO
class ActivateSO(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='activate')
        if self.entity.extras['ops_version'] == 'v2':
            self.repo_uri = self.entity.extras['repo_uri']
            self.host = urlparse(self.repo_uri).netloc.split('@')[1]
            if os.system('which git') != 0:
                raise EnvironmentError('Git is not available.')
        elif self.entity.extras['ops_version'] == 'v3':
            self.host = self.entity.extras['loc']

    def __is_complete(self, url):
        # XXX copy/paste code - merge the two places!
        heads = {
                'Content-type': 'text/occi',
                'Accept': 'application/occi+json',
                'X-Auth-Token': self.extras['token'],
                'X-Tenant-Name': self.extras['tenant_name'],
            }

        LOG.info('Checking app state at: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())

        r = http_retriable_request('GET', url, headers=heads, authenticate=True)
        attrs = json.loads(r.content)

        if len(attrs['attributes']) > 0:
            attr_hash = attrs['attributes']
            app_state = ''
            try:
                app_state = attr_hash['occi.app.state']
            except KeyError:
                pass

            LOG.info('Current service state: ' + str(app_state))
            if app_state == 'active':
                # check if it returns something valid instead of 503
                try:
                    tmpUrl = 'http://' + attr_hash['occi.app.url']
                except KeyError:
                    LOG.info(('App is not ready. app url is not yet set.'))
                    return False
                r = http_retriable_request('GET', tmpUrl, headers=heads, authenticate=True)
                if r.status_code == 200:
                    LOG.info('App is ready')
                    elapsed_time = time.time() - self.extras['occi.init.starttime']
                    del self.extras['occi.init.starttime']

                    infoDict = {
                                'so_id': self.entity.attributes['occi.core.id'],
                                'sm_name': self.entity.kind.term,
                                'so_phase': 'init',
                                'phase_event': 'done',
                                'response_time': elapsed_time,
                                'tenant': self.extras['tenant_name']
                                }
                    tmpJSON = json.dumps(infoDict)
                    LOG.debug(tmpJSON)
                    return True
                else:
                    LOG.info('App is not ready. app url returned: ' + r.status_code)
            else:
                LOG.info('App is not ready. Current state state: ' + app_state)
                return False



    def run(self):

        # this is wrong but required...
        if self.entity.extras['ops_version'] == 'v3':
            url = self.entity.attributes['occi.so.url']
            while not self.__is_complete(url):
                time.sleep(3)


        LOG.debug('ACTIVATE SO START')

        self.start_time = time.time()
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'activate',
                    'phase_event': 'start',
                    'response_time': 0,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)
        if self.entity.extras['ops_version'] == 'v2':
            # get the code of the bundle and push it to the git facilities
            # offered by OpenShift
            LOG.debug('Deploying SO Bundle to: ' + self.repo_uri)
            self.__deploy_app()

        LOG.debug('Activating the SO...')
        self.__init_so()

        self.entity.attributes['mcn.service.state'] = 'activate'

        return self.entity, self.extras

    def __deploy_app(self):
        """
            Deploy the local SO bundle
            assumption here
            - a git repo is returned
            - the bundle is not managed by git
        """
        # create temp dir...and clone the remote repo provided by OpS
        tmp_dir = tempfile.mkdtemp()
        LOG.debug('Cloning git repository: ' + self.repo_uri + ' to: ' + tmp_dir)
        cmd = ' '.join(['git', 'clone', self.repo_uri, tmp_dir])
        os.system(cmd)

        # Get the SO bundle
        bundle_loc = os.environ.get('BUNDLE_LOC', False)
        if not bundle_loc:
            bundle_loc = CONFIG.get('service_manager', 'bundle_location', '')
        if bundle_loc == '':
            raise Exception('No bundle_location parameter supplied in sm.cfg')
        LOG.debug('Bundle to add to repo: ' + bundle_loc)
        dir_util.copy_tree(bundle_loc, tmp_dir)

        self.__add_openshift_files(bundle_loc, tmp_dir)

        # add & push to OpenShift
        os.system(' '.join(['cd', tmp_dir, '&&', 'git', 'add', '-A']))
        os.system(' '.join(['cd', tmp_dir, '&&', 'git', 'commit', '-m', '"deployment of SO for tenant ' +
                            self.extras['tenant_name'] + '"', '-a']))
        LOG.debug('Pushing new code to remote repository...')
        os.system(' '.join(['cd', tmp_dir, '&&', 'git', 'push']))

        shutil.rmtree(tmp_dir)

    def __add_openshift_files(self, bundle_loc, tmp_dir):
        # put OpenShift stuff in place
        # build and pre_start_python comes from 'support' directory in bundle
        LOG.debug('Adding OpenShift support files from: ' + bundle_loc + '/support')

        # 1. Write build
        LOG.debug('Writing build to: ' + os.path.join(tmp_dir, '.openshift', 'action_hooks', 'build'))
        shutil.copyfile(bundle_loc+'/support/build', os.path.join(tmp_dir, '.openshift', 'action_hooks', 'build'))

        # 1. Write pre_start_python
        LOG.debug('Writing pre_start_python to: ' +
                  os.path.join(tmp_dir, '.openshift', 'action_hooks', 'pre_start_python'))

        pre_start_template = Template(filename=bundle_loc+'/support/pre_start_python')
        design_uri = CONFIG.get('service_manager', 'design_uri', '')
        content = pre_start_template.render(design_uri=design_uri)
        LOG.debug('Writing pre_start_python content as: ' + content)
        pre_start_file = open(os.path.join(tmp_dir, '.openshift', 'action_hooks', 'pre_start_python'), "w")
        pre_start_file.write(content)
        pre_start_file.close()

        os.system(' '.join(['chmod', '+x', os.path.join(tmp_dir, '.openshift', 'action_hooks', '*')]))

    # example request to the SO
    # curl -v -X PUT http://localhost:8051/orchestrator/default \
    #   -H 'Content-Type: text/occi' \
    #   -H 'Category: orchestrator; scheme="http://schemas.mobile-cloud-networking.eu/occi/service#"' \
    #   -H 'X-Auth-Token: '$KID \
    #   -H 'X-Tenant-Name: '$TENANT
    def __init_so(self):
        url = HTTP + self.host + '/orchestrator/default'
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
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'activate',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)
        #LOG.debug('ACTIVATE SO DONE, elapsed: %f' % elapsed_time)


class DeploySO(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='deploy')
        if self.entity.extras['ops_version'] == 'v2':
            self.repo_uri = self.entity.extras['repo_uri']
            self.host = urlparse(self.repo_uri).netloc.split('@')[1]
        elif self.entity.extras['ops_version'] == 'v3':
            self.host = self.entity.extras['loc']

    # example request to the SO
    # curl -v -X POST http://localhost:8051/orchestrator/default?action=deploy \
    #   -H 'Content-Type: text/occi' \
    #   -H 'Category: deploy; scheme="http://schemas.mobile-cloud-networking.eu/occi/service#"' \
    #   -H 'X-Auth-Token: '$KID \
    #   -H 'X-Tenant-Name: '$TENANT
    def run(self):
        # Deployment is done without any control by the client...
        # otherwise we won't be able to hand back a working service!
        #LOG.debug('DEPLOY SO START')

        self.start_time = time.time()
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'deploy',
                    'phase_event': 'start',
                    'response_time': 0,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        #LOG.debug('Deploying the SO bundle...')
        url = HTTP + self.host + '/orchestrator/default'
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
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'deploy',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)
        #LOG.debug('DEPLOY SO DONE, elapsed: %f' % elapsed_time)
        return self.entity, self.extras

    def deploy_complete(self, url):
        # XXX fugly - code copied from Resolver
        heads = {
            'Content-type': 'text/occi',
            'Accept': 'application/occi+json',
            'X-Auth-Token': self.extras['token'],
            'X-Tenant-Name': self.extras['tenant_name'],
        }

        LOG.info('checking service state at: ' + url)
        LOG.info('sending headers: ' + heads.__repr__())

        r = http_retriable_request('GET', url, headers=heads)
        attrs = json.loads(r.content)

        if len(attrs['attributes']) > 0:
            attr_hash = attrs['attributes']
            stack_state = ''
            try:
                stack_state = attr_hash['occi.mcn.stack.state']
            except KeyError:
                pass

            LOG.info('Current service state: ' + str(stack_state))
            if stack_state == 'CREATE_COMPLETE' or stack_state == 'UPDATE_COMPLETE':
                LOG.info('Stack is ready')
                return True
            else:
                LOG.info('Stack is not ready. Current state state: ' + stack_state)
                return False


class ProvisionSO(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='provision')
        if self.entity.extras['ops_version'] == 'v2':
            self.repo_uri = self.entity.extras['repo_uri']
            self.host = urlparse(self.repo_uri).netloc.split('@')[1]
        elif self.entity.extras['ops_version'] == 'v3':
            self.host = self.entity.extras['loc']

    def run(self):
        # this can only run until the deployment has complete!
        # this will block until run() returns
        #LOG.debug('PROVISION SO START')

        self.start_time = time.time()
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'provision',
                    'phase_event': 'start',
                    'response_time': 0,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        url = HTTP + self.host + '/orchestrator/default'

        # with stuff like this, we need to have a callback mechanism... this will block otherwise
        while not self.deploy_complete(url):
            time.sleep(13)

        params = {'action': 'provision'}
        heads = {
            'Category': 'provision; scheme="http://schemas.mobile-cloud-networking.eu/occi/service#"',
            'Content-Type': 'text/occi',
            'X-Auth-Token': self.extras['token'],
            'X-Tenant-Name': self.extras['tenant_name']}
        occi_attrs = self.extras['srv_prms'].service_parameters(self.state)
        if len(occi_attrs) > 0:
            LOG.info('Adding service-specific parameters to call... X-OCCI-Attribute: ' + occi_attrs)
            heads['X-OCCI-Attribute'] = occi_attrs
        LOG.debug('Provisioning SO with: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())
        http_retriable_request('POST', url, headers=heads, params=params)

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'provision',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)
        #LOG.debug('PROVISION SO DONE, elapsed: %f' % elapsed_time)
        self.entity.attributes['mcn.service.state'] = 'provision'
        return self.entity, self.extras

    def deploy_complete(self, url):
        # XXX fugly - code copied from Resolver
        heads = {
            'Content-type': 'text/occi',
            'Accept': 'application/occi+json',
            'X-Auth-Token': self.extras['token'],
            'X-Tenant-Name': self.extras['tenant_name'],
        }

        LOG.info('checking service state at: ' + url)
        LOG.info('sending headers: ' + heads.__repr__())

        r = http_retriable_request('GET', url, headers=heads)
        attrs = json.loads(r.content)

        if len(attrs['attributes']) > 0:
            attr_hash = attrs['attributes']
            stack_state = ''
            try:
                stack_state = attr_hash['occi.mcn.stack.state']
            except KeyError:
                pass

            LOG.info('Current service state: ' + str(stack_state))
            if stack_state == 'CREATE_COMPLETE' or stack_state == 'UPDATE_COMPLETE':
                LOG.info('Stack is ready')
                return True
            elif stack_state == 'CREATE_FAILED':
                raise RuntimeError('Heat stack creation failed.')
            else:
                LOG.info('Stack is not ready. Current state state: ' + stack_state)
                return False


class RetrieveSO(Task):

    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, 'retrieve')
        if self.entity.extras['ops_version'] == 'v2':
            self.repo_uri = self.entity.extras['repo_uri']
            self.host = urlparse(self.repo_uri).netloc.split('@')[1]
        elif self.entity.extras['ops_version'] == 'v3':
            self.host = self.entity.extras['loc']
        self.registry = self.extras['registry']

    def run(self):
        # example request to the SO
        # curl -v -X GET http://localhost:8051/orchestrator/default \
        #   -H 'X-Auth-Token: '$KID \
        #   -H 'X-Tenant-Name: '$TENANT

        self.start_time = time.time()
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'retrieve',
                    'phase_event': 'start',
                    'response_time': 0,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        if self.entity.attributes['mcn.service.state'] in ['activate', 'deploy', 'provision', 'update']:
            heads = {
                'Content-Type': 'text/occi',
                'Accept': 'text/occi',
                'X-Auth-Token': self.extras['token'],
                'X-Tenant-Name': self.extras['tenant_name']}
            LOG.info('Getting state of service orchestrator with: ' + self.host + '/orchestrator/default')
            LOG.info('Sending headers: ' + heads.__repr__())
            r = http_retriable_request('GET', HTTP + self.host + '/orchestrator/default', headers=heads)

            attrs = r.headers['x-occi-attribute'].split(', ')
            for attr in attrs:
                kv = attr.split('=')
                if kv[0] != 'occi.core.id':
                    if kv[1].startswith('"') and kv[1].endswith('"'):
                        kv[1] = kv[1][1:-1]  # scrub off quotes
                    self.entity.attributes[kv[0]] = kv[1]
                    LOG.debug('OCCI Attribute: ' + kv[0] + ' --> ' + kv[1])

            # Assemble the SIG
            svcinsts = ''
            try:
                svcinsts = self.entity.attributes['mcn.so.svcinsts']
                del self.entity.attributes['mcn.so.svcinsts']  # remove this, not be be used anywhere else
            except KeyError:
                LOG.warn('There was no service instance endpoints - ignore if not a composition.')
                pass

            if self.registry is None:
                LOG.error('No registry!')

            if len(svcinsts) > 0:
                svcinsts = svcinsts.split()  # all instance EPs
                for svc_loc in svcinsts:
                    # TODO get the service instance resource representation
                    # source resource is self.entity
                    compos = svc_loc.split('/')
                    key = '/' + compos[3] + '/' + compos[4]
                    target = Resource(key, Resource.kind, [])  # target resource
                    target.attributes['mcn.sm.endpoint'] = svc_loc
                    self.registry.add_resource(key, target, None)

                    key = '/link/'+str(uuid.uuid4())
                    link = Link(key, Link.kind, [], self.entity, target)
                    self.registry.add_resource(key, link, None)
                    self.entity.links.append(link)
        else:
            LOG.debug('Cannot GET entity as it is not in the activated, deployed or provisioned, updated state')

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'retrieve',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        return self.entity, self.extras


# can only be executed when provisioning is complete
class UpdateSO(Task):
    def __init__(self, entity, extras, updated_entity):
        Task.__init__(self, entity, extras, state='update')
        if self.entity.extras['ops_version'] == 'v2':
            self.repo_uri = self.entity.extras['repo_uri']
            self.host = urlparse(self.repo_uri).netloc.split('@')[1]
        elif self.entity.extras['ops_version'] == 'v3':
            self.host = self.entity.extras['loc']
        self.new = updated_entity

    def run(self):
        # take parameters from EEU and send them down to the SO instance
        # Trigger update on SO + service instance:
        #
        # $ curl -v -X POST http://localhost:8051/orchestrator/default \
        #       -H 'Content-Type: text/occi' \
        #       -H 'X-Auth-Token: '$KID \
        #       -H 'X-Tenant-Name: '$TENANT \
        #       -H 'X-OCCI-Attribute: occi.epc.attr_1="foo"'

        self.start_time = time.time()
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'update',
                    'phase_event': 'start',
                    'response_time': 0,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        url = HTTP + self.host + '/orchestrator/default'
        heads = {
            'Content-Type': 'text/occi',
            'X-Auth-Token': self.extras['token'],
            'X-Tenant-Name': self.extras['tenant_name']}

        occi_attrs = self.extras['srv_prms'].service_parameters(self.state)

        if len(occi_attrs) > 0:
            LOG.info('Adding service-specific parameters to call... X-OCCI-Attribute:' + occi_attrs)
            heads['X-OCCI-Attribute'] = occi_attrs

        if len(self.new.attributes) > 0:
            LOG.info('Adding updated parameters... X-OCCI-Attribute: ' + self.new.attributes.__repr__())
            for k, v in self.new.attributes.items():
                occi_attrs = occi_attrs + ', ' + k + '=' + v
                self.entity.attributes[k] = v
            heads['X-OCCI-Attribute'] = occi_attrs

        LOG.debug('Updating (Provisioning) SO with: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())

        http_retriable_request('POST', url, headers=heads)

        self.entity.attributes['mcn.service.state'] = 'update'

        #start thread here
        thread = Thread(target = deploy_complete, args = (url, self.start_time, self.extras, self.entity ))
        thread.start()

        return self.entity, self.extras


def deploy_complete(url, start_time, extras, entity):

    done = False

    while done == False:
        # XXX fugly - code copied from Resolver
        heads = {
            'Content-type': 'text/occi',
            'Accept': 'application/occi+json',
            'X-Auth-Token': extras['token'],
            'X-Tenant-Name': extras['tenant_name'],
        }

        LOG.info('checking service state at: ' + url)
        LOG.info('sending headers: ' + heads.__repr__())

        r = http_retriable_request('GET', url, headers=heads)
        attrs = json.loads(r.content)

        if len(attrs['attributes']) > 0:
            attr_hash = attrs['attributes']
            stack_state = ''
            try:
                stack_state = attr_hash['occi.mcn.stack.state']
            except KeyError:
                pass

            LOG.info('Current service state: ' + str(stack_state))
            if stack_state == 'CREATE_COMPLETE' or stack_state == 'UPDATE_COMPLETE':
                LOG.info('Stack is ready')
                elapsed_time = time.time() - start_time
                infoDict = {
                    'so_id': entity.attributes['occi.core.id'],
                    'sm_name': entity.kind.term,
                    'so_phase': 'update',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    'tenant': extras['tenant_name']
                    }
                tmpJSON = json.dumps(infoDict)
                LOG.debug(tmpJSON)
                done = True
            else:
                LOG.info('Stack is not ready. Current state state: ' + stack_state)
                done = False
                time.sleep(3)


class DestroySO(Task):
    def __init__(self, entity, extras):
        Task.__init__(self, entity, extras, state='destroy')
        if self.entity.extras['ops_version'] == 'v2':
            self.repo_uri = self.entity.extras['repo_uri']
            self.host = urlparse(self.repo_uri).netloc.split('@')[1]
        elif self.entity.extras['ops_version'] == 'v3':
            self.host = self.entity.extras['loc']
        self.nburl = os.environ.get('CC_URL', False)
        if not self.nburl:
            self.nburl = CONFIG.get('cloud_controller', 'nb_api', '')

    def run(self):
        # 1. dispose the active SO, essentially kills the STG/ITG
        # 2. dispose the resources used to run the SO
        # example request to the SO
        # curl -v -X DELETE http://localhost:8051/orchestrator/default \
        #   -H 'X-Auth-Token: '$KID \
        #   -H 'X-Tenant-Name: '$TENANT

        self.start_time = time.time()
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'destroy',
                    'phase_event': 'start',
                    'response_time': 0,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        url = HTTP + self.host + '/orchestrator/default'
        heads = {'X-Auth-Token': self.extras['token'],
                 'X-Tenant-Name': self.extras['tenant_name']}
        occi_attrs = self.extras['srv_prms'].service_parameters(self.state)
        if len(occi_attrs) > 0:
            LOG.info('Adding service-specific parameters to call... X-OCCI-Attribute:' + occi_attrs)
            heads['X-OCCI-Attribute'] = occi_attrs
        LOG.info('Disposing service orchestrator with: ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())

        http_retriable_request('DELETE', url, headers=heads)

        url = self.nburl + self.entity.identifier.replace('/' + self.entity.kind.term + '/', '/app/')
        heads = {'Content-Type': 'text/occi',
                 'X-Auth-Token': self.extras['token'],
                 'X-Tenant-Name': self.extras['tenant_name']}
        LOG.info('Disposing service orchestrator container via CC... ' + url)
        LOG.info('Sending headers: ' + heads.__repr__())
        http_retriable_request('DELETE', url, headers=heads, authenticate=True)

        elapsed_time = time.time() - self.start_time
        infoDict = {
                    'so_id': self.entity.attributes['occi.core.id'],
                    'sm_name': self.entity.kind.term,
                    'so_phase': 'destroy',
                    'phase_event': 'done',
                    'response_time': elapsed_time,
                    'tenant': self.extras['tenant_name']
                    }
        tmpJSON = json.dumps(infoDict)
        LOG.debug(tmpJSON)

        return self.entity, self.extras
