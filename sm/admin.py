from flask import Flask
import requests
import json

from pymongo import MongoClient
from sm.config import CONFIG, CONFIG_PATH

app = Flask('hurtle-sm')

print 'Using CONFIG_PATH %s' % CONFIG_PATH

stg_path = CONFIG.get('service_manager', 'manifest', False)
if not stg_path:
    raise RuntimeError('No STG specified in the configuration file.')
with open(stg_path) as stg_content:
    stg = json.load(stg_content)
service_shema = stg['service_type']
sm_name = service_shema.split('#')[1]


cc_url = CONFIG.get('cloud_controller', 'nb_api', False)
if not cc_url:
    raise RuntimeError('No Cloud Controller specified in the configuration file.')
db_host = 'localhost'


def get_mongo_connection():
    connection = MongoClient(db_host)
    resources_db = connection.resources_db
    return resources_db.resource_coll


@app.route('/')
def home():
    return '', 200


# curl -X POST $URL/update/self -> re-provisions CC (no effect)
# curl -X POST $URL/update/$NAME -> re-provisions the deploymentConfig $NAME, do this after a /build/$NAME was triggered
@app.route('/update/<name>', methods=['POST'])
def update(name):
    if name == 'self':
        response = requests.post(cc_url + '/build/%s' % sm_name)
        return response.content, response.status_code
    elif name == 'children':
        mongo_resources = get_mongo_connection()
        resources = mongo_resources.find_one()
        del resources['_id']
        urls = []
        if resources is not None:
            for key, resource in resources.iteritems():
                base_url = resource['extras']['loc']
                url = 'http://%s:8081/update/self' % base_url
                urls.append(url)
                print 'curl -v -X POST %s' % url
        if len(urls) > 0:
            return json.dumps({'urls': urls}), 200
        return 'not yet implemented', 500
    else:
        return 'not implemented', 500


def server(host, port):
    app.run(host=host, port=port, debug=False)
