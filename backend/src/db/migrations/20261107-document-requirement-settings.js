// Legacy Express migration. The active FastAPI application does not execute it.
import VisaRequirement from "../../models/VisaRequirement.js";

const DEFAULT_FORMATS = ["pdf", "jpg", "jpeg", "png"];

export const migrateDocumentRequirements = async () => {
  const maximumSize = Math.min(
    10 * 1024 * 1024,
    Number(process.env.MAX_FILE_SIZE) || 10 * 1024 * 1024,
  );
  const requirements = await VisaRequirement.find({})
    .select("_id displayOrder maxFileSize acceptedFormats status")
    .lean();
  const updates = requirements
    .filter(
      (requirement) =>
        requirement.displayOrder === undefined ||
        requirement.maxFileSize === undefined ||
        requirement.acceptedFormats === undefined ||
        requirement.status === undefined,
    )
    .map((requirement) => {
      const $set = {};
      if (requirement.displayOrder === undefined) $set.displayOrder = 0;
      if (requirement.maxFileSize === undefined)
        $set.maxFileSize = maximumSize;
      if (requirement.acceptedFormats === undefined)
        $set.acceptedFormats = DEFAULT_FORMATS;
      if (requirement.status === undefined) $set.status = "ACTIVE";
      return VisaRequirement.updateOne({ _id: requirement._id }, { $set });
    });
  await Promise.all(updates);
  return updates.length;
};
