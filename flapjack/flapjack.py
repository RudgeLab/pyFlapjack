import requests
from requests.exceptions import HTTPError
from requests_jwt import JWTAuth
import websockets
import asyncio
import json
import plotly
from plotly.io import from_json, read_json
from tqdm import tqdm
import pandas as pd
import numpy as np

plot_option_keys = [
    'normalize',
    'subplots',
    'markers',
    'plot'
]

index_params = [
    'biomass_signal',
    'ref_signal',
    'analyte'
]

replace_columns_with_ids = [
    'dnas'
]

# Main class that provides access to flapjack
class Flapjack():
    models = [
        'study',
        'assay',
        'strain',
        'media',
        'vector',
        'dna',
        'signal',
        'chemical'
    ]
    
    def __init__(self, url_base = 'localhost:8000'):
        self.http_url_base = 'http://' + url_base
        self.ws_url_base = 'ws://' + url_base
        self.access_token = None
        self.refresh_token = None
        self.username = None

    def __del__(self):
        self.log_out()
        
    def handle_response(self, s):
        return True
        
    
    def log_in(self, username, password):
        try:
            s = requests.post(
                self.http_url_base+'/api/auth/log_in/', 
                data={
                    'username':username, 
                    'password':password
                }
            )
        except:
            print(f'Log in failed.')
        else:
            if self.handle_response(s):
                self.username = username
                data = s.json()
                self.access_token = data['access']
                self.refresh_token = data['refresh']
                self.username = username

    def log_out(self, username):
        if self.username:
            try:
                s = requests.post(
                    self.http_url_base+'/api/auth/log_out/', 
                    data={
                        'username':username
                    }
                )
            except:
                print(f'Log out failed for {self.username}.')
        else:
            print('No user logged in.')
                
    def refresh(self):
        s = requests.post(
            self.http_url_base+'/api/auth/refresh/', 
            data={'refresh': self.refresh_token}
        )
        self.access_token = s.json()['access']

    def create(self, model, **kwargs):
        url = self.http_url_base + f'/api/{model}/'
        s = requests.post(
                url,
                headers={'Authorization': 'Bearer ' + self.access_token},
                data=kwargs
            )
        if s:
            return pd.DataFrame([s.json()])
        else:
            print(f'Problem creating {model}:')
            print(s.text)
            return False

    def delete(self, model, id, confirm=True):
        if confirm:
            confirmed = input(f'If you are sure you want to delete {model} with id={id} type "yes"')
            if confirmed != 'yes':
                return False
        url = self.http_url_base + f'/api/{model}/{id}/'
        s = requests.delete(
                url,
                headers={'Authorization': 'Bearer ' + self.access_token}
            )
        if s:
            return True
        else:
            print(f'Problem deleting {model} id={id}:')
            print(s.text)
            return False


    def query(self, model, **kwargs):
        if model not in self.models:
            print(f'Error: model {model} does not exist')
            return
        self.refresh()
        results = []
        url = self.http_url_base + f'/api/{model}/'
        while url:
            s = requests.get(
                url,
                headers={'Authorization': 'Bearer ' + self.access_token},
                params=kwargs
            )
            data = s.json()
            results.extend(data['results'])
            url = data['next']
        df = pd.DataFrame(results)
        # Convert ids from np.int64 to int
        df.index = df.index.astype(np.int)
        # Convert columns with objects to their ids
        for col in df.columns:
            if col in replace_columns_with_ids:
                df[col] = [[dd['id'] for dd in d] for d in df[col]]
        return df
    
    def parse_params(self, **kwargs):
        # Get search params and convert ids to python int
        params = {
            model + 'Ids': [
                int(id) for id in kwargs.get(model, [])
            ] for model in self.models
        }
        if len(params)==0:
            # Empty query params
            print('Empty query, no results to return')
            return {}
        
        # Extract plot parameters
        plot_options = {
            key: kwargs[key] for key in kwargs if key in plot_option_keys
        }
        # Extract analysis paramters
        analysis_params = {
            key: kwargs[key] for key in kwargs if key not in self.models and key not in plot_option_keys
        }
        # Convert indices to python int for JSON serialization
        for key in index_params:
            if key in analysis_params:
                param = analysis_params[key]
                try:
                    analysis_params[key] = int(analysis_params[key])
                except:
                    print(f'Error: must supply a single integer ID value for {key}')
                    return {}
                    
        # Combine into single dict
        params['plotOptions'] = plot_options
        params['analysis'] = analysis_params
        return params

    def analysis(self, **kwargs):
        return asyncio.run(self._analysis(**kwargs))
        
    async def _analysis(self, **kwargs):
        self.refresh()
        uri = self.ws_url_base + '/ws/analysis/analysis?token=' + self.access_token

        # Get the parameter dict from arguments
        params = self.parse_params(**kwargs)
        if len(params)==0:
            return
        
        # Data to send in request
        payload = {
            "type":"analysis",
            "parameters": params
        }
        async with websockets.connect(uri, max_size=1e8) as websocket:
            await websocket.send(json.dumps(payload))
            response_json = await websocket.recv()
            response_data = json.loads(response_json)
            if response_data['type']=='error':
                msg = response_data['data']['message']
                print(f'Error: {msg}')

            with tqdm(total=100) as pbar:
                progress_prev = 0
                while response_data['type']=='progress_update':
                    progress = response_data['data']['progress']
                    response_json = await websocket.recv()
                    response_data = json.loads(response_json)
                    pbar.update(progress-progress_prev)
                    progress_prev = progress
                pbar.update(100-progress)
                pbar.close()
            if response_data['type']=='analysis':
                df_json = response_data['data']
                if df_json:
                    return pd.read_json(df_json)
                else:
                    return
            else:
                print('Error: the server returned an invalid response')
                return
            
    def plot(self, **kwargs):
        return asyncio.run(self._plot(**kwargs))

    async def _plot(self, **kwargs): #data_filter, plot_options):
        self.refresh()
        uri = self.ws_url_base + '/ws/plot/plot?token=' + self.access_token

        # Get the parameter dict from arguments
        params = self.parse_params(**kwargs)
        if len(params)==0:
            return
        
        # Data to send in request
        payload = {
            "type":"plot",
            "parameters": params
        }
        async with websockets.connect(uri, max_size=1e8) as websocket:
            await websocket.send(json.dumps(payload))
            response_json = await websocket.recv()
            response_data = json.loads(response_json)
            with tqdm(total=100) as pbar:
                progress_prev = 0
                while response_data['type']=='progress_update':
                    progress = response_data['data']['progress']
                    response_json = await websocket.recv()
                    response_data = json.loads(response_json)
                    pbar.update(progress-progress_prev)
                    progress_prev = progress
                pbar.update(100-progress)
                pbar.close()
            if response_data['type']=='plot_data':
                fig_json = response_data['data']['figure']
                if fig_json:
                    return from_json(fig_json)
                else:
                    return
            else:
                print('Error: the server returned an invalid response')
                return



