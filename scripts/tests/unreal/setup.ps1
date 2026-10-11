# Copyright (C) 2026 - Joseph M Wilcox
param(
    [Parameter(Mandatory=$true)][string]$Engine,
    [Parameter(Mandatory=$true)][string]$ReferenceProject,
    [Parameter(Mandatory=$true)][string[]]$ReferenceAssets,
    [string]$Work = (Join-Path $PSScriptRoot '../../../work/rig_validation')
)
$ErrorActionPreference = 'Stop'
$Work = [IO.Path]::GetFullPath($Work)
$referenceContent = Join-Path (Split-Path ([IO.Path]::GetFullPath($ReferenceProject))) 'Content'
if ($Work.StartsWith((Split-Path $referenceContent), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The validation workspace must be outside the reference project.'
}
$source = Join-Path $Work 'Source/RigValidation'
New-Item -ItemType Directory -Force $source | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'RigValidationLibrary.h') $source
Copy-Item (Join-Path $PSScriptRoot 'RigValidationLibrary.cpp') $source
@'
{"FileVersion":3,"Modules":[{"Name":"RigValidation","Type":"Runtime","LoadingPhase":"Default"}],"Plugins":[{"Name":"PythonScriptPlugin","Enabled":true},{"Name":"InterchangeEditor","Enabled":true},{"Name":"SkeletalMeshModelingTools","Enabled":true}]}
'@ | Set-Content (Join-Path $Work 'RigValidation.uproject')
@'
using UnrealBuildTool;
public class RigValidationEditorTarget : TargetRules {
 public RigValidationEditorTarget(TargetInfo Target) : base(Target) { Type=TargetType.Editor; DefaultBuildSettings=BuildSettingsVersion.Latest; IncludeOrderVersion=EngineIncludeOrderVersion.Latest; ExtraModuleNames.Add("RigValidation"); }
}
'@ | Set-Content (Join-Path $Work 'Source/RigValidationEditor.Target.cs')
@'
using UnrealBuildTool;
public class RigValidation : ModuleRules {
 public RigValidation(ReadOnlyTargetRules Target) : base(Target) { PCHUsage=PCHUsageMode.UseExplicitOrSharedPCHs; PublicDependencyModuleNames.AddRange(new[]{"Core", "CoreUObject", "Engine"}); }
}
'@ | Set-Content (Join-Path $source 'RigValidation.Build.cs')
@'
#include "Modules/ModuleManager.h"
IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, RigValidation, "RigValidation");
'@ | Set-Content (Join-Path $source 'RigValidation.cpp')
foreach ($relative in $ReferenceAssets) {
    $assetSource = [IO.Path]::GetFullPath((Join-Path $referenceContent $relative))
    $assetDestination = [IO.Path]::GetFullPath((Join-Path (Join-Path $Work 'Content') $relative))
    if (-not $assetSource.StartsWith($referenceContent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or -not $assetDestination.StartsWith((Join-Path $Work 'Content') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'ReferenceAssets must contain relative paths inside Content.'
    }
    if (-not (Test-Path -LiteralPath $assetSource -PathType Leaf)) { throw "Missing reference asset: $relative" }
    $destination = Join-Path (Join-Path $Work 'Content') $relative
    New-Item -ItemType Directory -Force (Split-Path $destination) | Out-Null
    Copy-Item -LiteralPath (Join-Path $referenceContent $relative) -Destination $destination
}
& (Join-Path $Engine 'Engine/Build/BatchFiles/Build.bat') RigValidationEditor Win64 Development "-Project=$Work/RigValidation.uproject" -WaitMutex -NoHotReloadFromIDE -MaxParallelActions=4
if ($LASTEXITCODE -ne 0) { throw "Validation project build failed: $LASTEXITCODE" }
Write-Output "Validation project: $Work/RigValidation.uproject"
