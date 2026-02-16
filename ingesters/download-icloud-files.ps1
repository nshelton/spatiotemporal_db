# Force iCloud to download all Arc JSON files
$path = "D:\iCloudDrive\iCloud~com~bigpaua~LearnerCoacher\Export\JSON\Daily"
$files = Get-ChildItem "$path\*.json.gz"

Write-Host "Found $($files.Count) files"
Write-Host "Triggering iCloud to download..."

$downloaded = 0
foreach ($file in $files) {
    try {
        # Reading the file triggers iCloud to download it
        $null = Get-Content $file.FullName -TotalCount 1 -ErrorAction SilentlyContinue
        $downloaded++
        if ($downloaded % 50 -eq 0) {
            Write-Host "Processed $downloaded files..."
        }
    }
    catch {
        Write-Host "Warning: Could not access $($file.Name): $_"
    }
}

Write-Host "Done! Triggered download for $downloaded files"
Write-Host "Files may take a few minutes to fully download from iCloud"
