from dataclasses import dataclass
from pathlib import Path
import tomllib

@dataclass
class Config:
    banks: Path = Path('private/banks')
    state: Path = Path('private/runtime/state.sqlite3')
    jobs: Path = Path('private/runtime/jobs')
    index: Path = Path('private/index')
    base_url: str = 'http://127.0.0.1:8770'
    host: str = '127.0.0.1'
    port: int = 8770
    origins: tuple = ('http://127.0.0.1:8770',)
    download_ttl: int = 1800
    max_questions: int = 300
    per_user_limit: int = 3
    queue_limit: int = 20
    rate_limit: int = 60

    @classmethod
    def load(cls,path=None):
        data=tomllib.loads(Path(path).read_text(encoding='utf-8')) if path else {}
        for key in ('banks','state','jobs','index'):
            if key in data: data[key]=Path(data[key]).expanduser().absolute()
        if 'origins' in data: data['origins']=tuple(data['origins'])
        return cls(**data)
