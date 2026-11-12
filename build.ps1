# Gera o EvCantos.exe, instala para o usuario atual e cria os atalhos.
#   powershell -ExecutionPolicy Bypass -File build.ps1            (build + instalar + migrar dados + atalhos)
#   powershell -ExecutionPolicy Bypass -File build.ps1 -NoInstall (so gera o .exe)
#   powershell -ExecutionPolicy Bypass -File build.ps1 -NoMigrate (nao copia config/historico desta pasta)
# Tudo fora do OneDrive e fora do AppData (o Windows pode redirecionar o AppData de alguns programas):
#   build:    %USERPROFILE%\EvCantosBuild
#   programa: %USERPROFILE%\EvCantos\app
#   dados:    %USERPROFILE%\EvCantos\data   (config, historico, chaves, atualizacoes, musicas)
param([switch]$NoInstall, [switch]$NoMigrate)
$ErrorActionPreference = "Stop"

$proj = $PSScriptRoot
$work = Join-Path $env:USERPROFILE "EvCantosBuild"
$root = Join-Path $env:USERPROFILE "EvCantos"
$inst = Join-Path $root "app"
$data = Join-Path $root "data"
$venv = Join-Path $work "venv"
$py   = Join-Path $venv "Scripts\python.exe"
$exe  = Join-Path $inst "EvCantos.exe"

# Programas externos escrevem avisos em stderr; no PowerShell 5.1 isso viraria erro. Aqui so o codigo de saida conta.
function Invoke-Native([string]$what, [scriptblock]$cmd) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $cmd 2>&1 | ForEach-Object { "$_" } | Write-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) { throw "$what falhou (codigo $code)" }
}

New-Item -ItemType Directory -Force $work | Out-Null

Write-Host "[1/6] Ambiente isolado em $venv"
if (-not (Test-Path $py)) { Invoke-Native "criar o ambiente (python -m venv)" { python -m venv $venv } }
Invoke-Native "atualizar o pip" { & $py -m pip install --quiet --upgrade pip }
Invoke-Native "instalar pywebview e pyinstaller" { & $py -m pip install --quiet pywebview pyinstaller }

Write-Host "[2/6] Icone"
$icon = Join-Path $proj "evcantos.ico"
if (-not (Test-Path $icon)) { Push-Location $proj; & $py make_icon.py; Pop-Location }

Write-Host "[3/6] Separando o codigo do app (vai como arquivos, para poder ser atualizado sem trocar o .exe)"
$stage = Join-Path $work "stage"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
$code = Join-Path $stage "code"
New-Item -ItemType Directory -Force $code | Out-Null
foreach ($f in "app", "bot", "core", "corners", "oddspapi", "store", "tg", "version") { Copy-Item (Join-Path $proj "$f.py") $code }
Copy-Item (Join-Path $proj "web") (Join-Path $code "web") -Recurse
Copy-Item (Join-Path $proj "version.py") $stage
Copy-Item (Join-Path $proj "config.default.json") (Join-Path $stage "config.json")

Write-Host "[4/6] Gerando o EvCantos.exe (PyInstaller) - leva alguns minutos"
# o app e importado em tempo de execucao, entao o empacotador nao enxerga o que ele usa da biblioteca padrao
$hidden = "argparse", "collections", "dataclasses", "datetime", "glob", "hmac", "html", "http.server", "json", "logging",
          "os", "pathlib", "re", "secrets", "sqlite3", "threading", "time", "typing", "urllib.error", "urllib.parse",
          "urllib.request", "webbrowser" | ForEach-Object { "--hidden-import=$_" }
$dist = Join-Path $work "dist"
Invoke-Native "PyInstaller" {
    & $py -m PyInstaller --noconfirm --clean --onedir --noconsole --name EvCantos --icon $icon `
        --distpath $dist --workpath (Join-Path $work "build") --specpath $work `
        "--add-data=$code;code" "--add-data=$(Join-Path $stage 'version.py');." "--add-data=$(Join-Path $stage 'config.json');." `
        @hidden (Join-Path $proj "desktop.py")
}
$built = Join-Path $dist "EvCantos"
if (-not (Test-Path (Join-Path $built "EvCantos.exe"))) { throw "EvCantos.exe nao foi gerado" }
Write-Host "      gerado em $built"
if ($NoInstall) { Write-Host "Pronto (sem instalar)."; return }

Write-Host "[5/6] Instalando em $inst"
if (Get-Process -Name EvCantos -ErrorAction SilentlyContinue) { throw "feche o EV CANTOS antes de instalar" }
New-Item -ItemType Directory -Force $inst | Out-Null
robocopy $built $inst /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "falha ao copiar para $inst (robocopy $LASTEXITCODE)" }
$global:LASTEXITCODE = 0
if (-not $NoMigrate) {
    New-Item -ItemType Directory -Force (Join-Path $data "papi") | Out-Null
    foreach ($rel in "config.json", "bot.db", "papi\markets10.json", "papi\tournaments10.json", "papi\.budget.json") {
        $from = Join-Path $proj $rel; $to = Join-Path $data $rel
        if ((Test-Path $from) -and -not (Test-Path $to)) { Copy-Item $from $to; Write-Host "      dados copiados: $rel" }
    }
}

Write-Host "[6/6] Atalhos"
function New-AppShortcut($path) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $s.TargetPath = $exe; $s.WorkingDirectory = $inst; $s.IconLocation = "$exe,0"; $s.Description = "EV CANTOS"
    $s.Save()
    Write-Host "      $path"
}
New-AppShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "EV CANTOS.lnk")
New-AppShortcut (Join-Path ([Environment]::GetFolderPath("Programs")) "EV CANTOS.lnk")
Write-Host "Pronto. Abra o EV CANTOS e fixe o icone na barra de tarefas (botao direito no icone > Fixar na barra de tarefas)."
