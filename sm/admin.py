from flask import Flask
import requests
import json
import time
import os

from pymongo import MongoClient
from sm.config import CONFIG, CONFIG_PATH

app = Flask('hurtle-sm')

print 'Using CONFIG_PATH %s' % CONFIG_PATH

stg_path = CONFIG.get('service_manager', 'manifest', False)
if not stg_path:
    raise RuntimeError('No STG specified in the configuration file.')
with open(stg_path) as stg_content:
    stg = json.load(stg_content)
so_buildconfig_name = stg['service_type'].split('#')[1].replace('_', '-')

service_shema = stg['service_type']
sm_name = service_shema.split('#')[1]

cc_url = os.environ.get('CC_URL', False)
cc_admin_url = os.environ.get('CC_ADMIN_URL', False)
mongo_service_name = os.environ.get('MONGO_SERVICE_NAME', 'SAMPLE_SM')
db_host_key = service_name + '_SERVICE_HOST'
db_port_key = service_name + '_SERVICE_PORT'
print 'getting mongo connection details via env: %s & %s' % (db_host_key, db_port_key)
db_host = os.environ.get(db_host_key)
db_port = os.environ.get(db_port_key)
print 'resolved mongo host to %s:%s' % (db_host, db_port)


def print_response(response):
    if response.content:
        print '> %i: %s' % (response.status_code, response.content)
    else:
        print '> %i' % response.status_code


def get_mongo_connection():
    connection = MongoClient(db_host, int(db_port))
    resources_db = connection.resources_db
    return resources_db.resource_coll


@app.route('/')
def home():
    return '', 200


# curl -X POST $URL/update/self -> updates self, rebuilds / redeploys bc/dc of this sm
# curl -X POST $URL/update/children -> propagates /update/self to all spawned SOs
@app.route('/update/<name>', methods=['POST'])
def update(name):
    if name == 'self':
        print '### Updating myself...'
        url = cc_admin_url + '/build/%s' % sm_name
        print 'curl -v -X POST %s' % url
        response = requests.post(url)
        return response.content, response.status_code
    elif name == 'children':
        print '### Updating my SOs...'

        # build
        print '### Building new SO Image...'
        url = cc_admin_url + '/build/%s' % so_buildconfig_name
        print 'curl -v -X POST %s' % url
        response = requests.post(url)
        print_response(response)
        if response.status_code != 201:
            return response.content, response.status_code

        build_name = json.loads(response.content)['build_name']
        print '### Build %s started' % build_name
        # poll until build is complete
        done = False
        while not done:
            url = cc_admin_url + '/build/%s/%s' % (so_buildconfig_name, build_name)
            print 'curl -v -X GET %s' % url
            response = requests.get(url)
            print_response(response)

            if response.status_code != 200:
                return response.content, response.status_code
            content = json.loads(response.content)
            if content['phase'] == 'Complete':
                done = True
            else:
                time.sleep(5)

        print '### Build done'
        print '### Telling my SOs to redeploy themselves'
        # propagate update to SOs
        mongo_resources = get_mongo_connection()
        resources = mongo_resources.find_one()
        del resources['_id']
        urls = []
        if resources is not None:
            for key, resource in resources.iteritems():
                base_url = resource['extras']['loc']
                url = 'http://%s/update/self' % base_url
                urls.append(url)
                print 'curl -v -X POST %s' % url
                response = requests.post(url)
                print_response(response)
                if response.status_code != 200:
                    return response.content, response.status_code
        if len(urls) > 0:
            print '### Update Signal sent to all SOs!'
            return json.dumps({'urls': urls}), 200
        return 'not yet implemented', 500
    else:
        return 'not implemented', 500


def server(host, port):
    all_ok = True
    if not cc_url:
        all_ok = False
        print 'WARNING: No Cloud Controller specified.'
    if not cc_admin_url:
        all_ok = False
        print 'WARNING: No Cloud Controller Admin URL.'
    if not db_host:
        all_ok = False
        print 'WARNING: No MongoDB host specified.'

    if all_ok:
        print 'Admin API listening on %s:%i' % (host, port)
        app.run(host=host, port=port, debug=False)
    else:
        print 'WARNING: will not start Admin API!'
