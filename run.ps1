$rendererPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $rendererPython -PathType Leaf)) {
    $rendererPython = Join-Path $PSScriptRoot '..\pocket-extraction\tools\python\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $rendererPython -PathType Leaf)) {
    throw 'Ambiente Python não encontrado. Execute setup.cmd ou use o ambiente local em pocket-extraction\tools\python.'
}
& $rendererPython (Join-Path $PSScriptRoot 'render_cards.py') @args
exit $LASTEXITCODE
