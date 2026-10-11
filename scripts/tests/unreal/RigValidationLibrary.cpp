// Copyright (C) 2026 - Joseph M Wilcox
#include "RigValidationLibrary.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Engine/SkeletalMesh.h"
#include "Animation/AnimSequence.h"
#include "Components/SkeletalMeshComponent.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Rendering/SkeletalMeshLODRenderData.h"
FString URigValidationLibrary::ValidateAnimation(UObject* Context, USkeletalMesh* Mesh, UAnimSequence* Animation) {
 UWorld* World=GEngine->GetWorldFromContextObject(Context,EGetWorldErrorMode::ReturnNull);
 if (!World || !Mesh || !Animation || Mesh->GetSkeleton()!=Animation->GetSkeleton()) return TEXT("{\"success\":false,\"error\":\"missing world/mesh/animation or skeleton mismatch\"}");
 AActor* Actor=World->SpawnActor<AActor>();
 auto* Component=NewObject<USkeletalMeshComponent>(Actor);
 Actor->SetRootComponent(Component);Component->SetSkeletalMesh(Mesh);Component->RegisterComponent();
 Component->VisibilityBasedAnimTickOption=EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
 Component->SetAnimationMode(EAnimationMode::AnimationSingleNode);Component->PlayAnimation(Animation,false);
 auto Sample=[&](float Time,TArray<FVector3f>& Positions) {
  Component->SetPosition(Time,false);Component->TickAnimation(0,false);Component->RefreshBoneTransforms();
  TArray<FMatrix44f> Matrices;Component->GetCurrentRefToLocalMatrices(Matrices,0);
  auto* Render=Component->GetSkeletalMeshRenderData();auto* Weights=Component->GetSkinWeightBuffer(0);
  if(!Render || Render->LODRenderData.Num()==0 || !Weights) return false;
  USkinnedMeshComponent::ComputeSkinnedPositions(Component,Positions,Matrices,Render->LODRenderData[0],*Weights);
  return Positions.Num()>0;
 };
 TArray<FVector3f> Before,After;
 bool Ok=Sample(Animation->GetPlayLength()*.1f,Before) && Sample(Animation->GetPlayLength()*.6f,After) && Before.Num()==After.Num();
 double Max=0;int32 Changed=0;
 if(Ok) for(int32 I=0;I<Before.Num();I++) { if(Before[I].ContainsNaN()||After[I].ContainsNaN()){Ok=false;break;} double Distance=(Before[I]-After[I]).Size();Max=FMath::Max(Max,Distance);if(Distance>.1)Changed++; }
 double MaxPairLengthChange=0;
 if(Ok) for(int32 I=0;I<Before.Num();I+=FMath::Max(1,Before.Num()/200)) {int32 J=(I+Before.Num()/3)%Before.Num();MaxPairLengthChange=FMath::Max(MaxPairLengthChange,(double)FMath::Abs((Before[I]-Before[J]).Size()-(After[I]-After[J]).Size()));}
 Ok=Ok && Max>1 && Changed>100 && MaxPairLengthChange>.1;
 Component->DestroyComponent();Actor->Destroy();
 return FString::Printf(TEXT("{\"success\":%s,\"vertices\":%d,\"vertices_moved_over_1mm\":%d,\"max_vertex_displacement_cm\":%.6f,\"max_pair_distance_change_cm\":%.6f}"),Ok?TEXT("true"):TEXT("false"),Before.Num(),Changed,Max,MaxPairLengthChange);
}
