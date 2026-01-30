param(
    [int]$Port = 8000,
    [string]$RuleName = 'Programa Chamados HTTP'
)

Write-Host "Verificando regra de firewall para a porta $Port..."

$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($rule) {
    Set-NetFirewallRule -DisplayName $RuleName -Enabled True -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Domain,Private,Public
    Write-Host "Regra existente atualizada para permitir a porta $Port."
} else {
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Domain,Private,Public `
        -Description 'Permite o acesso ao servidor Programa Chamados pela rede local.'
    Write-Host "Regra criada e habilitada para a porta $Port."
}
