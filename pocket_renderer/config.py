from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re

CODE_ROOT = Path(__file__).resolve().parents[1]
# Extracted originals and existing runtimes stay outside the source project.
PROJECT_ROOT = CODE_ROOT.parent / 'pocket-extraction'
LANGUAGES = ('en-US', 'pt-BR', 'ja-JP')

def normalize_language(value: str) -> str:
    normalized = value.lower().replace('_', '-')
    aliases = {'en': 'en-US', 'en-us': 'en-US', 'english': 'en-US',
               'pt': 'pt-BR', 'pt-br': 'pt-BR', 'portugues': 'pt-BR',
               'ja': 'ja-JP', 'ja-jp': 'ja-JP', 'jp': 'ja-JP',
               'japanese': 'ja-JP', 'japones': 'ja-JP', 'japonês': 'ja-JP'}
    if normalized not in aliases:
        raise ValueError('Idioma inválido. Use en-US, pt-BR ou ja-JP.')
    return aliases[normalized]


def normalize_languages(value: str) -> list:
    """Resolve one or more card languages, keeping legacy all/both selectors."""
    tokens = re.split(r'[,\s]+', value.strip())
    if not all(tokens):
        raise ValueError('Select at least one card language.')
    selected = set()
    for token in tokens:
        if token.casefold() == 'all':
            selected.update(LANGUAGES)
        elif token.casefold() == 'both':
            selected.update(('en-US', 'pt-BR'))
        else:
            try:
                selected.add(normalize_language(token))
            except ValueError as error:
                raise ValueError('Unsupported card language: ' + token) from error
    return [language for language in LANGUAGES if language in selected]


@dataclass(frozen=True)
class RenderConfig:
    root: Path = PROJECT_ROOT
    output: Path = PROJECT_ROOT / 'renders'
    cache: Path = PROJECT_ROOT / 'render-cache'
    width: int = 700
    height: int = 976
    overwrite: bool = False
    stored_glyphs_only: bool = False
    quality: str = 'large'

    def validate(self):
        if self.quality not in ('auto','large','native'):
            raise ValueError('Qualidade inválida. Use auto, large ou native.')
        # The measured original front UVs set this conservative native-detail cap.
        if not 1 <= self.width <= 700 or not 1 <= self.height <= 976:
            raise ValueError('A saída deve ficar até 700 × 976 para preservar o limite de detalhe nativo validado.')
        return self
