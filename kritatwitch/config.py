from typing import Dict
import json

Config = Dict[str, str]

def load_config(path: str) -> Config:
    with open(path) as file:
        return json.load(file)

def save_config(path: str, config: Config) -> None:
    with open(path, 'w') as file:
        return json.dump(config, file, sort_keys=True, indent=4)
