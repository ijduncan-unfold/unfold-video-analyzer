"""Costume identifications are suggestions about fiction, never real identities."""
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def name_key(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


CHARACTERS = {
    name_key(name): name for name in ('Spider-Man', 'Iron Man', 'Deadpool', 'Batman',
        'Harley Quinn', 'Wonder Woman', 'Darth Vader', 'Stormtrooper', 'Sailor Moon',
        'Captain America', 'Wolverine', 'Superman', 'Goku', 'Naruto Uzumaki')
}
FRANCHISES = {
    name_key(name): name for name in ('Marvel', 'DC', 'Star Wars', 'Sailor Moon',
        'Dragon Ball', 'Naruto', 'Pokémon', 'Demon Slayer', 'One Piece', 'The Legend of Zelda')
}
FRANCHISES.update({'marvelcomics': 'Marvel', 'marvelcinematicuniverse': 'Marvel',
                   'mcu': 'Marvel', 'dccomics': 'DC', 'pokemon': 'Pokémon'})


def canonical_name(value: str, franchise=False) -> str:
    value = ' '.join(value.split())
    if name_key(value) in {'unknown', 'unidentified', 'none', 'na'}:
        return ''
    return (FRANCHISES if franchise else CHARACTERS).get(name_key(value), value)


# MCU is also an editorial shot type (medium close-up), so only use that alias
# in the explicit franchise field. Preserve ordinary footage search semantics.
_SEARCH_ALIASES = {key: value for key, value in {**CHARACTERS, **FRANCHISES}.items() if key != 'mcu'}
_SEARCH_PATTERN = re.compile(r'(?<!\w)(?:' + '|'.join(map(re.escape, _SEARCH_ALIASES)) + r')(?!\w)', re.I)


def normalize_query(value: str) -> str:
    return _SEARCH_PATTERN.sub(lambda match: _SEARCH_ALIASES[match.group().casefold()], value)


class CosplayIdentification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    character: str = Field(default='', max_length=200)
    franchise: str = Field(default='', max_length=200)
    costume_variant: str = Field(default='', max_length=300)
    visible_details: str = Field(default='', max_length=2000)
    activity: str = Field(default='', max_length=1000)
    possible_characters: list[str] = Field(default_factory=list, max_length=10)
    identification: Literal['suggested', 'confirmed', 'unknown'] = 'unknown'

    @field_validator('character')
    @classmethod
    def character_name(cls, value):
        return canonical_name(value)

    @field_validator('franchise')
    @classmethod
    def franchise_name(cls, value):
        return canonical_name(value, franchise=True)

    @field_validator('possible_characters')
    @classmethod
    def candidate_names(cls, values):
        result = {}
        for value in values:
            if len(value) > 200:
                raise ValueError('Character names must be under 200 characters')
            value = canonical_name(value)
            if value:
                result.setdefault(name_key(value), value)
        return list(result.values())

    @model_validator(mode='after')
    def uncertain_identity(self):
        if not self.character:
            self.identification = 'unknown'
        elif self.identification == 'unknown':
            self.possible_characters = list(dict.fromkeys([self.character, *self.possible_characters]))[:10]
            self.character = ''
        return self
