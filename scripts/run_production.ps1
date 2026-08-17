$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo
$port = if ($env:HTSA_PORT) { $env:HTSA_PORT } else { "5000" }
$threads = if ($env:HTSA_WEB_THREADS) { $env:HTSA_WEB_THREADS } else { "4" }
python -m waitress --listen="127.0.0.1:$port" --threads="$threads" wsgi:app
