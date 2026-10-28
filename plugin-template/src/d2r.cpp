// Binds d2r.h's function table to the running image (D2R 3.3.0 build 93847 under D2RLoader 1.3.1 only).
#include "d2r.h"

namespace d2r {

Api api {};

namespace {
template <typename Fn>
void BindOne(Fn& slot, uint64_t exeBase, uint64_t rva) noexcept {
	slot = reinterpret_cast<Fn>(exeBase + rva);
}
}  // namespace

void Bind(uint64_t exeBase) noexcept {
	BindOne(api.getSkillRecord, exeBase, RvaGetSkillRecord);
	BindOne(api.evalSkillCalc, exeBase, RvaEvalSkillCalc);
	BindOne(api.skillTargetXY, exeBase, RvaSkillTargetXY);
	BindOne(api.skillTargetUnit, exeBase, RvaSkillTargetUnit);
	BindOne(api.createMissile, exeBase, RvaCreateMissile);
	BindOne(api.missileRecord, exeBase, RvaMissileRecord);
	BindOne(api.missileCount, exeBase, RvaMissileCount);
	BindOne(api.unitSeed, exeBase, RvaUnitSeed);
	BindOne(api.setUnitSkillFlag, exeBase, RvaSetUnitSkillFlag);
	BindOne(api.applyMeleeHit, exeBase, RvaApplyMeleeHit);
	BindOne(api.unitStat, exeBase, RvaUnitStat);
	BindOne(api.unitStatNoParam, exeBase, RvaUnitStatNoParam);
	BindOne(api.summonPet, exeBase, RvaSummonPet);
	BindOne(api.setPetLevel, exeBase, RvaSetPetLevel);
	BindOne(api.petSkillStats, exeBase, RvaPetSkillStats);
	BindOne(api.addPetToParty, exeBase, RvaAddPetToParty);
}

auto Bound() noexcept -> bool {
	return api.getSkillRecord != nullptr;
}

}  // namespace d2r
