$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$repo = Join-Path $projectRoot "models\CatVTON"
if (-not (Test-Path -LiteralPath (Join-Path $repo ".git"))) {
    git clone --depth 1 https://github.com/Zheng-Chong/CatVTON.git $repo
}

if (-not (Test-Path -LiteralPath ".venv-catvton\Scripts\python.exe")) {
    python -m venv --system-site-packages .venv-catvton
}

& ".venv-catvton\Scripts\python.exe" -m pip install --disable-pip-version-check -r "backend\adapters\catvton-requirements.txt"
& ".venv-catvton\Scripts\python.exe" -c "import torch; assert torch.cuda.is_available(); assert torch.cuda.get_device_capability(0)[0] >= 12; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))"
& ".venv-catvton\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'$repo'); from model.pipeline import CatVTONPipeline; from model.cloth_masker import AutoMasker; print('CatVTON imports passed')"

@{
    source_commit = (git -C $repo rev-parse HEAD)
    runtime = ".venv-catvton"
    note = "Weights download into models/cache/catvton on first try-on."
} | ConvertTo-Json | Set-Content -LiteralPath "models\.catvton-ready.json"

Write-Host "CatVTON runtime is ready. Model weights will download on the first try-on." -ForegroundColor Green
