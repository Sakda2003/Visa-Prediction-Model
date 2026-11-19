// Legacy Express migration. The active FastAPI application does not execute it.
import mongoose from "mongoose";
import VisaRequirement from "../../models/VisaRequirement.js";
import VisaType from "../../models/VisaType.js";
import {
  JAPAN_TOURIST_REQUIREMENTS,
  JAPAN_TOURIST_VISA_CODE,
} from "../japanTouristRequirements.js";

const CODE_ALIASES = {
  employment_income_letter: ["photo"],
  hotel_booking: ["national_id"],
  flight_ticket: ["fixed_deposit"],
  other_supporting_documents: ["spouse_bank_statement", "additional_supporting_documents", "supporting_documents"],
};

export const migrateJapanTouristRequirements = async () => {
  const visaType = await VisaType.findOne({
    visaTypeCode: JAPAN_TOURIST_VISA_CODE,
  }).lean();
  if (!visaType) return 0;

  const current = await VisaRequirement.find({
    visaTypeId: visaType._id,
  }).lean();
  const claimed = new Set();
  let changed = 0;

  for (const [displayOrder, config] of JAPAN_TOURIST_REQUIREMENTS.entries()) {
    const isUnclaimed = (item) => !claimed.has(String(item._id));
    const hasTargetName = (item) =>
      item.name?.trim().toLowerCase() === config.name.toLowerCase();
    // Prefer the record already using the new code/name. A legacy alias can
    // coexist with it; selecting that alias first and renaming it would violate
    // the unique { visaTypeId, name } index during migration.
    const existing = current.find(
      (item) =>
        isUnclaimed(item) && (item.code === config.code || hasTargetName(item)),
    ) || current.find(
      (item) => isUnclaimed(item) && (CODE_ALIASES[config.code] || []).includes(item.code),
    );
    const fields = {
      visaTypeId: visaType._id,
      countryId: visaType.countryId,
      name: config.name,
      code: config.code,
      description: config.description,
      required: config.required,
      conditional: config.conditional,
      weight: config.weight,
      displayOrder,
      maxFileSize: config.maxFileSize,
      acceptedFormats: config.acceptedFormats,
      allowMultiple: config.allowMultiple,
      source: config.source,
      sourceStatus: config.sourceStatus,
      status: config.status,
      effectiveFrom: existing?.effectiveFrom || new Date(),
    };

    if (existing) {
      claimed.add(String(existing._id));
      await VisaRequirement.updateOne({ _id: existing._id }, { $set: fields });
    } else {
      await VisaRequirement.create({
        _id: new mongoose.Types.ObjectId(),
        ...fields,
      });
    }
    changed += 1;
  }

  const staleIds = current
    .filter((item) => !claimed.has(String(item._id)) && item.status !== "ARCHIVED")
    .map((item) => item._id);
  if (staleIds.length) {
    await VisaRequirement.updateMany(
      { _id: { $in: staleIds } },
      { $set: { status: "ARCHIVED" } },
    );
    changed += staleIds.length;
  }

  return changed;
};
