// Copyright (C) 2026 - Joseph M Wilcox
#pragma once
#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "RigValidationLibrary.generated.h"
class USkeletalMesh;
class UAnimSequence;
UCLASS()
class URigValidationLibrary : public UBlueprintFunctionLibrary {
 GENERATED_BODY()
 public:
 UFUNCTION(BlueprintCallable, meta=(WorldContext="Context"))
 static FString ValidateAnimation(UObject* Context, USkeletalMesh* Mesh, UAnimSequence* Animation);
};
