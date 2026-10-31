param([string]$Src,[string]$Dst)
$ErrorActionPreference = 'Stop'
$w = New-Object -ComObject Word.Application
$w.Visible = $false
try {
  $d = $w.Documents.Open($Src, $false, $true)
  $d.SaveAs([ref]$Dst, [ref]17)
  $d.Close($false)
} finally {
  $w.Quit()
}
