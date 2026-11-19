import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, readFile, readdir, rm, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { after } from "node:test";
import { validateUploadedDocument } from "../src/utils/documentValidation.js";
import {
  JAPAN_TOURIST_REQUIREMENTS,
  JAPAN_TOURIST_VISA_CODE,
} from "../src/db/japanTouristRequirements.js";
import { analyzeJapanDocument } from "../src/utils/japanDocumentChecks.js";

const privateDirectory = await mkdtemp(
  path.join(os.tmpdir(), "visadoc-private-test-"),
);
process.env.DOCUMENT_STORAGE_DIR = privateDirectory;

const { uploadDocument } = await import(
  "../src/controllers/document.controller.js"
);
const { reviewDocument } = await import(
  "../src/controllers/document.controller.js"
);
const { createCustomer } = await import(
  "../src/controllers/customer.controller.js"
);
const { deleteApplication } = await import(
  "../src/controllers/application.controller.js"
);
const {
  createVisaType,
  createApplication,
  createRequirement,
  deleteVisaType,
  listRequirements,
  updateRequirement,
  updateVisaType,
} = await import(
  "../src/controllers/visa.controller.js"
);
const { runVerification } = await import(
  "../src/controllers/verification.controller.js"
);

test("defines the Japan tourist checklist with document weights totaling 100", () => {
  assert.equal(JAPAN_TOURIST_VISA_CODE, "VISA-JP-TOU-SIN-V90D-15D-S-KHM");
  assert.deepEqual(
    JAPAN_TOURIST_REQUIREMENTS.map(({ name, required, conditional }) => ({
      name,
      required,
      conditional,
    })),
    [
      { name: "Passport Copy", required: true, conditional: false },
      { name: "Employment / Income Letter", required: true, conditional: false },
      { name: "Hotel Booking", required: true, conditional: false },
      {
        name: "Bank Statement (Last 3 Months)",
        required: true,
        conditional: false,
      },
      { name: "Flight Ticket", required: true, conditional: false },
      { name: "Other Supporting Documents", required: true, conditional: false },
    ],
  );
  assert.equal(
    JAPAN_TOURIST_REQUIREMENTS.reduce((sum, item) => sum + item.weight, 0),
    100,
  );
});

test("checks passport dates and only permits staff verification when they pass", async () => {
  const application = {
    _id: "application-jp",
    customer: "customer-jp",
    returnDate: "2026-01-31",
    requirementSnapshot: [
      {
        requirementId: "passport-jp",
        code: "passport",
        name: "Passport Copy",
        required: true,
      },
    ],
  };
  const document = {
    _id: "document-passport-jp",
    applicationId: application._id,
    requirementId: "passport-jp",
    status: "PENDING",
    extractedFields: {
      passportDateOfBirth: "1990-01-02",
      passportExpiryDate: "2026-07-30",
    },
  };
  global.__demoStore = {
    applications: [application],
    customers: [{ _id: "customer-jp", dateOfBirth: "1990-01-01" }],
    documents: [document],
    requirements: [],
  };

  const mismatch = response();
  await reviewDocument(
    {
      params: { id: document._id },
      body: { status: "VERIFIED" },
      user: { id: "staff-1", role: "STAFF" },
    },
    mismatch,
  );
  assert.equal(mismatch.statusCode, 422);
  assert.match(mismatch.body.message, /date of birth does not match/i);
  assert.equal(document.status, "PENDING");

  const expiryBoundary = response();
  await reviewDocument(
    {
      params: { id: document._id },
      body: {
        status: "VERIFIED",
        extractedFields: {
          passportDateOfBirth: "1990-01-01",
          passportExpiryDate: "2026-07-30",
        },
      },
      user: { id: "staff-1", role: "STAFF" },
    },
    expiryBoundary,
  );
  assert.equal(expiryBoundary.statusCode, 422);
  assert.match(expiryBoundary.body.message, /at least 6 months/i);

  const valid = response();
  await reviewDocument(
    {
      params: { id: document._id },
      body: {
        status: "VERIFIED",
        extractedFields: {
          passportDateOfBirth: "1990-01-01",
          passportExpiryDate: "2026-07-31",
        },
      },
      user: { id: "staff-1", role: "STAFF" },
    },
    valid,
  );
  assert.equal(valid.statusCode, 200);
  assert.equal(valid.body.document.status, "VERIFIED");
});

test("automatically checks high-confidence OCR passport candidates without marking authenticity verified", async () => {
  const application = {
    _id: "application-ocr-passport",
    customer: "customer-ocr-passport",
    returnDate: "2026-01-31",
    requirementSnapshot: [
      {
        requirementId: "passport-ocr",
        code: "passport",
        name: "Passport Copy",
        required: true,
        weight: 20,
      },
    ],
  };
  const document = {
    _id: "document-ocr-passport",
    applicationId: application._id,
    requirementId: "passport-ocr",
    status: "PENDING",
    ocrStatus: "success",
    ocrConfidence: 0.95,
    extractedFields: {
      passportDateOfBirth: "1990-01-01",
      passportExpiryDate: "2026-07-31",
    },
  };
  global.__demoStore = {
    applications: [application],
    customers: [{ _id: application.customer, dateOfBirth: "1990-01-01" }],
    documents: [document],
    requirements: [],
    verifications: [],
  };

  const result = response();
  await runVerification({ body: { applicationId: application._id } }, result);

  assert.equal(result.statusCode, 200);
  assert.equal(result.body.verification.status, "PASS");
  assert.match(
    result.body.verification.results[0].explanationEn,
    /staff must still confirm document authenticity/i,
  );
  assert.equal(document.status, "PENDING");
});

test("flags a passport name that does not match the applicant form", () => {
  const common = {
    requirementCode: "passport",
    applicant: { fullName: "Sok Dara", dateOfBirth: "1990-01-01" },
    application: { returnDate: "2026-01-31" },
    documentStatus: "VERIFIED",
    extractedFields: {
      passportDateOfBirth: "1990-01-01",
      passportExpiryDate: "2026-07-31",
    },
  };
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: { ...common.extractedFields, passportFullName: "Dara Sok" },
    }).status,
    "PASS",
  );
  const mismatch = analyzeJapanDocument({
    ...common,
    extractedFields: { ...common.extractedFields, passportFullName: "Sok Vanna" },
  });
  assert.equal(mismatch.status, "FAIL");
  assert.match(mismatch.message, /full name does not match/i);
});

test("checks bank-statement coverage and USD balance without treating blank values as zero", () => {
  const bankMonthlySummaries = [
    {
      openingBalance: "4000",
      totalCredits: "1000",
      totalDebits: "500",
      closingBalance: "4500",
    },
    {
      openingBalance: "4500",
      totalCredits: "1000",
      totalDebits: "500",
      closingBalance: "5000",
    },
    {
      openingBalance: "5000",
      totalCredits: "1000",
      totalDebits: "500",
      closingBalance: "5500",
    },
  ];
  const common = {
    requirementCode: "bank_statement",
    application: { returnDate: "2026-08-01" },
    documentStatus: "VERIFIED",
  };
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-01",
        statementEndDate: "2026-07-01",
        closingBalance: "5500",
        currency: "USD",
        bankMonthlySummaries,
      },
    }).status,
    "PASS",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-02",
        statementEndDate: "2026-07-01",
        closingBalance: "5500",
        currency: "USD",
        bankMonthlySummaries,
      },
    }).status,
    "FAIL",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-01",
        statementEndDate: "2026-07-01",
        closingBalance: "",
        currency: "USD",
        bankMonthlySummaries,
      },
    }).status,
    "MANUAL_REVIEW",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-01",
        statementEndDate: "2026-07-01",
        closingBalance: "5500",
        currency: "USD",
        bankMonthlySummaries: [
          bankMonthlySummaries[0],
          {
            ...bankMonthlySummaries[1],
            openingBalance: "4600",
            closingBalance: "5100",
          },
          bankMonthlySummaries[2],
        ],
      },
    }).status,
    "MANUAL_REVIEW",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-01",
        statementEndDate: "2026-07-01",
        closingBalance: "3999.99",
        currency: "USD",
        bankMonthlySummaries: [
          bankMonthlySummaries[0],
          bankMonthlySummaries[1],
          { ...bankMonthlySummaries[2], closingBalance: "3999.99" },
        ],
      },
    }).status,
    "FAIL",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-01",
        statementEndDate: "2026-07-01",
        closingBalance: "5500",
        currency: "USD",
      },
    }).status,
    "MANUAL_REVIEW",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-01",
        statementEndDate: "2026-07-01",
        closingBalance: "5500",
        currency: "USD",
        bankMonthlySummaries: [
          bankMonthlySummaries[0],
          bankMonthlySummaries[1],
          { ...bankMonthlySummaries[2], openingBalance: "7000" },
        ],
      },
    }).status,
    "MANUAL_REVIEW",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: {
        statementStartDate: "2026-04-01",
        statementEndDate: "2026-07-01",
        closingBalance: "4300",
        currency: "USD",
        bankMonthlySummaries: [
          bankMonthlySummaries[0],
          { ...bankMonthlySummaries[1], openingBalance: "4500", closingBalance: "4400" },
          {
            openingBalance: "4400",
            totalCredits: "900",
            totalDebits: "1000",
            closingBalance: "4300",
          },
        ],
      },
    }).status,
    "MANUAL_REVIEW",
  );
});

test("requires three complete monthly summaries before bank-statement verification", async () => {
  const application = {
    _id: "application-bank",
    customer: "customer-bank",
    returnDate: "2026-08-01",
    requirementSnapshot: [
      {
        requirementId: "bank-jp",
        code: "bank_statement",
        name: "Bank Statement (Last 3 Months)",
        required: true,
      },
    ],
  };
  const document = {
    _id: "document-bank",
    applicationId: application._id,
    requirementId: "bank-jp",
    status: "PENDING",
    extractedFields: {
      statementStartDate: "2026-04-01",
      statementEndDate: "2026-07-01",
      closingBalance: "5500",
      currency: "USD",
    },
  };
  global.__demoStore = {
    applications: [application],
    customers: [{ _id: "customer-bank" }],
    documents: [document],
    requirements: [],
  };

  const result = response();
  await reviewDocument(
    {
      params: { id: document._id },
      body: { status: "VERIFIED" },
      user: { id: "staff-1", role: "STAFF" },
    },
    result,
  );
  assert.equal(result.statusCode, 422);
  assert.match(result.body.message, /all 3 statement months/i);
  assert.equal(document.status, "PENDING");

  const verified = response();
  await reviewDocument(
    {
      params: { id: document._id },
      body: {
        status: "VERIFIED",
        extractedFields: {
          bankMonthlySummaries: [
            {
              openingBalance: "4000",
              totalCredits: "1000",
              totalDebits: "500",
              closingBalance: "4500",
            },
            {
              openingBalance: "4500",
              totalCredits: "1000",
              totalDebits: "500",
              closingBalance: "5000",
            },
            {
              openingBalance: "5000",
              totalCredits: "1000",
              totalDebits: "500",
              closingBalance: "5500",
            },
          ],
        },
      },
      user: { id: "staff-1", role: "STAFF" },
    },
    verified,
  );
  assert.equal(verified.statusCode, 200);
  assert.equal(verified.body.document.status, "VERIFIED");
  assert.equal(
    verified.body.document.extractedFields.bankMonthlySummaries.length,
    3,
  );
});

test("deletes an application and its linked private documents and verification history", async () => {
  const applicationId = "application-delete-test";
  const documentId = "document-delete-test";
  const filePath = path.join(privateDirectory, "delete-test.pdf");
  await writeFile(filePath, pdf);
  global.__demoStore = {
    applications: [{ _id: applicationId, applicationNumber: "VD-DELETE-1" }],
    documents: [
      {
        _id: documentId,
        applicationId,
        storagePath: filePath,
      },
      {
        _id: "document-other-application",
        applicationId: "application-keep",
        storagePath: path.join(privateDirectory, "keep.pdf"),
      },
    ],
    verifications: [
      { _id: "verification-delete", applicationId },
      { _id: "verification-keep", applicationId: "application-keep" },
    ],
  };

  const result = response();
  await deleteApplication(
    { params: { id: applicationId } },
    result,
  );
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.deletedDocuments, 1);
  assert.equal(global.__demoStore.applications.length, 0);
  assert.deepEqual(
    global.__demoStore.documents.map((item) => item._id),
    ["document-other-application"],
  );
  assert.deepEqual(
    global.__demoStore.verifications.map((item) => item._id),
    ["verification-keep"],
  );
  assert.equal((await readdir(privateDirectory)).includes("delete-test.pdf"), false);
});

test("checks fixed-deposit amount, currency, and maturity date", () => {
  const common = {
    requirementCode: "fixed_deposit",
    application: { returnDate: "2026-01-31" },
    documentStatus: "VERIFIED",
  };
  const fields = {
    depositAmount: "4000",
    currency: "USD",
    maturityDate: "2026-04-29",
  };
  assert.equal(
    analyzeJapanDocument({ ...common, extractedFields: fields }).status,
    "FAIL",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: { ...fields, maturityDate: "2026-04-30" },
    }).status,
    "PASS",
  );
  assert.equal(
    analyzeJapanDocument({
      ...common,
      extractedFields: { ...fields, currency: "KHR" },
    }).status,
    "MANUAL_REVIEW",
  );
});

after(async () => {
  delete global.__demoStore;
  await rm(privateDirectory, { recursive: true, force: true });
});

const pdf = Buffer.from("%PDF-1.7\nvisa document");
const requirement = {
  requirementId: "requirement-passport",
  name: "Passport",
  required: true,
  acceptedFormats: ["pdf"],
  maxFileSize: 1024,
  status: "ACTIVE",
};

const response = () => ({
  statusCode: 200,
  body: null,
  status(code) {
    this.statusCode = code;
    return this;
  },
  json(body) {
    this.body = body;
    return this;
  },
  type() {
    return this;
  },
  set() {
    return this;
  },
  send(body) {
    this.body = body;
    return this;
  },
});

test("validates file extension, MIME, signature, configured format, and size", () => {
  const base = {
    originalname: "passport.pdf",
    mimetype: "application/pdf",
    size: pdf.length,
    buffer: pdf,
  };
  assert.deepEqual(
    validateUploadedDocument(base, requirement, 1024),
    { format: "pdf", mimeType: "application/pdf", size: pdf.length },
  );
  assert.match(
    validateUploadedDocument(
      { ...base, originalname: "passport.jpg" },
      requirement,
      1024,
    ).error,
    /extension and MIME type/,
  );
  assert.match(
    validateUploadedDocument(
      { ...base, buffer: Buffer.from("not a PDF"), size: 9 },
      requirement,
      1024,
    ).error,
    /content does not match/,
  );
  assert.match(
    validateUploadedDocument(
      { ...base, size: 1025, buffer: Buffer.alloc(1025, 65) },
      requirement,
      2048,
    ).error,
    /exceeds/,
  );
});

test("stores a validated document privately and associates it with its application", async () => {
  global.__demoStore = {
    applications: [],
    customers: [
      {
        _id: "customer-1",
        fullName: "Example Applicant",
        email: "applicant@example.com",
        phone: "+1 555 0100",
        nationality: "Canadian",
        passportNumber: "A1234567",
        dateOfBirth: "1990-01-01",
        address: "Ottawa",
      },
    ],
    visaTypes: [
      {
        _id: "visa-type-1",
        active: true,
        destination: "Japan",
        category: "Tourist",
        countryId: "country-1",
        visaTypeCode: "VISA-JP-TOU",
      },
    ],
    requirements: [
      {
        _id: requirement.requirementId,
        visaTypeId: "visa-type-1",
        countryId: "country-1",
        ...requirement,
      },
    ],
    requirementVersions: [],
    documents: [],
    verifications: [],
  };
  const applicationResponse = response();
  await createApplication(
    {
      body: { customer: "customer-1", visaType: "visa-type-1" },
      user: { role: "ADMIN" },
    },
    applicationResponse,
  );
  assert.equal(applicationResponse.statusCode, 201);
  const applicationId = applicationResponse.body.application._id;
  const res = response();
  await uploadDocument(
    {
      file: {
        originalname: "passport.pdf",
        mimetype: "application/pdf",
        size: pdf.length,
        buffer: pdf,
      },
      body: {
        applicationId,
        requirementId: requirement.requirementId,
      },
      user: { id: "admin-1", role: "ADMIN" },
    },
    res,
    (error) => {
      throw error;
    },
  );

  assert.equal(res.statusCode, 201);
  assert.equal(res.body.document.applicationId, applicationId);
  assert.equal(res.body.document.requirementId, requirement.requirementId);
  assert.equal("storagePath" in res.body.document, false);
  const savedNames = await readdir(privateDirectory);
  assert.equal(savedNames.length, 1);
  assert.match(savedNames[0], /^[0-9a-f-]+\.pdf$/i);
  assert.deepEqual(await readFile(path.join(privateDirectory, savedNames[0])), pdf);

  const previous = global.__demoStore.documents[0];
  const replacementPdf = Buffer.from("%PDF-1.7\nreplacement document");
  const replacementResponse = response();
  await uploadDocument(
    {
      file: {
        originalname: "replacement.pdf",
        mimetype: "application/pdf",
        size: replacementPdf.length,
        buffer: replacementPdf,
      },
      body: {
        applicationId,
        requirementId: requirement.requirementId,
        replaceDocumentId: previous._id,
      },
      user: { id: "admin-1", role: "ADMIN" },
    },
    replacementResponse,
    (error) => {
      throw error;
    },
  );
  assert.equal(replacementResponse.statusCode, 201);
  assert.equal(global.__demoStore.documents.length, 1);
  assert.notEqual(global.__demoStore.documents[0]._id, previous._id);
  const afterReplacement = await readdir(privateDirectory);
  assert.equal(afterReplacement.length, 1);
  assert.deepEqual(
    await readFile(path.join(privateDirectory, afterReplacement[0])),
    replacementPdf,
  );
  const uploaded = global.__demoStore.documents[0];
  const verificationResponse = response();
  await runVerification(
    { body: { applicationId } },
    verificationResponse,
  );
  assert.equal(verificationResponse.statusCode, 200);
  assert.equal(
    verificationResponse.body.verification.results.find(
      (item) => item.requirementId === requirement.requirementId,
    ).documentId,
    uploaded._id,
  );
  assert.equal(
    Number.isFinite(
      verificationResponse.body.verification.completenessScore,
    ),
    true,
  );
});

test("sends supported document uploads to OCR and saves extracted candidate fields", async () => {
  const previousServiceUrl = process.env.AI_SERVICE_URL;
  let requestBody = "";
  const ocrServer = createServer((request, result) => {
    request.on("data", (chunk) => {
      requestBody += chunk.toString();
    });
    request.on("end", () => {
      result.writeHead(200, { "content-type": "application/json" });
      result.end(
        JSON.stringify({
          status: "success",
          confidence: 0.96,
          message: null,
          fields: {
            passportDateOfBirth: "1990-01-01",
            passportExpiryDate: "2030-01-01",
          },
        }),
      );
    });
  });
  await new Promise((resolve) => ocrServer.listen(0, "127.0.0.1", resolve));
  process.env.AI_SERVICE_URL = `http://127.0.0.1:${ocrServer.address().port}`;
  const application = {
    _id: "application-ocr-upload",
    customer: "customer-ocr-upload",
    visaTypeId: "visa-ocr",
    requirementSnapshot: [
      {
        requirementId: "passport-ocr-upload",
        code: "passport",
        name: "Passport Copy",
        required: true,
        acceptedFormats: ["pdf"],
        maxFileSize: 1024,
      },
    ],
  };
  global.__demoStore = {
    applications: [application],
    customers: [],
    requirements: [],
    documents: [],
  };

  try {
    const result = response();
    await uploadDocument(
      {
        file: {
          originalname: "passport.pdf",
          mimetype: "application/pdf",
          size: pdf.length,
          buffer: pdf,
        },
        body: {
          applicationId: application._id,
          requirementId: "passport-ocr-upload",
        },
        user: { id: "staff-ocr", role: "STAFF" },
      },
      result,
      (error) => {
        throw error;
      },
    );

    assert.equal(result.statusCode, 201);
    assert.match(requestBody, /name="requirement_code"\r\n\r\npassport/);
    assert.equal(result.body.document.ocrStatus, "success");
    assert.equal(result.body.document.ocrConfidence, 0.96);
    assert.equal(
      result.body.document.extractedFields.passportDateOfBirth,
      "1990-01-01",
    );
    assert.equal("storagePath" in result.body.document, false);
    await unlink(global.__demoStore.documents[0].storagePath);
  } finally {
    await new Promise((resolve, reject) =>
      ocrServer.close((error) => (error ? reject(error) : resolve())),
    );
    if (previousServiceUrl === undefined) delete process.env.AI_SERVICE_URL;
    else process.env.AI_SERVICE_URL = previousServiceUrl;
  }
});

test("application creation permits incomplete applicant fields and travel dates", async () => {
  global.__demoStore = {
    customers: [
      {
        _id: "customer-1",
        fullName: "Example Applicant",
        email: "applicant@example.com",
        phone: "+1 555 0100",
        nationality: "Canadian",
        passportNumber: "A1234567",
        dateOfBirth: "1990-01-01",
        address: "Ottawa",
      },
    ],
    visaTypes: [
      {
        _id: "visa-type-1",
        active: true,
        countryCode: "JP",
        destination: "Japan",
        category: "Tourist",
        countryId: "country-1",
        visaTypeCode: "VISA-JP-TOU",
      },
    ],
    requirements: [
      {
        _id: requirement.requirementId,
        visaTypeId: "visa-type-1",
        ...requirement,
      },
    ],
    applications: [],
  };
  const res = response();
  const missingDatesResponse = response();
  await createApplication(
    {
      body: { customer: "customer-1", visaType: "visa-type-1" },
      user: { role: "ADMIN" },
    },
    missingDatesResponse,
  );
  assert.equal(missingDatesResponse.statusCode, 201);
  assert.equal(missingDatesResponse.body.application.travelStartDate, "");
  assert.equal(missingDatesResponse.body.application.returnDate, "");

  await createApplication(
    {
      body: {
        customer: "customer-1",
        visaType: "visa-type-1",
        travelStartDate: "2026-04-01",
        returnDate: "2026-04-15",
      },
      user: { role: "ADMIN" },
    },
    res,
  );
  assert.equal(res.statusCode, 201);
  assert.equal(res.body.application.requirementSnapshot.length, 1);
  assert.equal(
    res.body.application.requirementSnapshot[0].requirementId,
    requirement.requirementId,
  );

  global.__demoStore.customers[0].email = "invalid-email";
  const invalidEmailResponse = response();
  await createApplication(
    {
      body: { customer: "customer-1", visaType: "visa-type-1" },
      user: { role: "ADMIN" },
    },
    invalidEmailResponse,
  );
  assert.equal(invalidEmailResponse.statusCode, 400);
  assert.match(invalidEmailResponse.body.message, /email address is invalid/i);

  global.__demoStore.customers[0].email = "applicant@example.com";
  global.__demoStore.customers[0].address = "";
  const incompleteResponse = response();
  await createApplication(
    {
      body: {
        customer: "customer-1",
        visaType: "visa-type-1",
        travelStartDate: "2026-04-01",
        returnDate: "2026-04-15",
      },
      user: { role: "ADMIN" },
    },
    incompleteResponse,
  );
  assert.equal(incompleteResponse.statusCode, 201);
});

test("visa notes persist and deleting a referenced visa archives its requirements", async () => {
  global.__demoStore = {
    applications: [],
    countries: [
      {
        _id: "country-jp",
        name: "Japan",
        isoCode: "JP",
        active: true,
      },
    ],
    customers: [],
    documents: [],
    requirements: [],
    requirementVersions: [],
    verifications: [],
    visaTypes: [],
  };
  const createResponse = response();
  await createVisaType(
    {
      body: {
        countryId: "country-jp",
        category: "Tourist",
        entryType: "Single",
        validity: "90 Days",
        validityDays: 90,
        allowedStayDays: 15,
        visaClass: "S",
        applicantNationality: "KHM",
        additionalNotes: "Use the current application form.",
      },
      user: { id: "admin-1", role: "ADMIN" },
    },
    createResponse,
  );
  assert.equal(createResponse.statusCode, 201);
  const visaType = createResponse.body.visaType;
  assert.equal(
    visaType.additionalNotes,
    "Use the current application form.",
  );

  const updateResponse = response();
  await updateVisaType(
    {
      params: { id: visaType._id },
      body: { additionalNotes: "Confirm the travel dates." },
    },
    updateResponse,
  );
  assert.equal(updateResponse.statusCode, 200);
  assert.equal(
    updateResponse.body.visaType.additionalNotes,
    "Confirm the travel dates.",
  );

  const requirementResponse = response();
  await createRequirement(
    {
      body: {
        visaTypeId: visaType._id,
        name: "Passport",
        required: true,
        weight: 10,
        acceptedFormats: ["pdf"],
        maxFileSize: 1024,
      },
      user: { id: "admin-1", role: "ADMIN" },
    },
    requirementResponse,
  );
  assert.equal(requirementResponse.statusCode, 201);

  const deleteResponse = response();
  await deleteVisaType(
    {
      params: { id: visaType._id },
      user: { id: "admin-1", role: "ADMIN" },
    },
    deleteResponse,
  );
  assert.equal(deleteResponse.statusCode, 200);
  assert.equal(deleteResponse.body.archived, true);
  assert.equal(global.__demoStore.visaTypes[0].active, false);
  assert.equal(global.__demoStore.requirements[0].status, "ARCHIVED");
});

test("Admin requirement settings are saved, ordered, and can be disabled", async () => {
  global.__demoStore = {
    visaTypes: [
      { _id: "visa-type-1", countryId: "country-1", active: true },
      { _id: "visa-type-2", countryId: "country-2", active: true },
    ],
    requirements: [],
    requirementVersions: [],
    applications: [],
  };
  const create = async (name, displayOrder) => {
    const res = response();
    await createRequirement(
      {
        body: {
          visaTypeId: "visa-type-1",
          name,
          weight: 10,
          required: name === "Passport",
          acceptedFormats: ["pdf"],
          maxFileSize: 2048,
          displayOrder,
        },
        user: { id: "admin-1", role: "ADMIN" },
      },
      res,
    );
    assert.equal(res.statusCode, 201);
    return res.body.requirement;
  };
  const second = await create("Bank statement", 2);
  const first = await create("Passport", 1);
  assert.equal(first.maxFileSize, 2048);
  assert.deepEqual(first.acceptedFormats, ["pdf"]);

  const duplicate = response();
  await createRequirement(
    {
      body: {
        visaTypeId: "visa-type-1",
        name: "passport",
        weight: 20,
        required: false,
        acceptedFormats: ["jpg"],
        maxFileSize: 4096,
        displayOrder: 3,
      },
      user: { id: "admin-1", role: "ADMIN" },
    },
    duplicate,
  );
  assert.equal(duplicate.statusCode, 409);
  assert.equal(duplicate.body.requirementId, first._id);

  const listed = response();
  listRequirements(
    {
      query: { visaTypeId: "visa-type-1" },
      user: { role: "ADMIN" },
    },
    listed,
  );
  assert.deepEqual(
    listed.body.requirements.map((item) => item.name),
    ["Passport", "Bank statement"],
  );

  const disabled = response();
  await updateRequirement(
    {
      params: { id: second._id },
      body: {
        visaTypeId: "visa-type-2",
        status: "ARCHIVED",
      },
      user: { id: "admin-1", role: "ADMIN" },
    },
    disabled,
  );
  assert.equal(disabled.statusCode, 200);
  assert.equal(disabled.body.requirement.status, "ARCHIVED");
  assert.equal(disabled.body.requirement.visaTypeId, "visa-type-2");
  assert.equal(disabled.body.requirement.countryId, "country-2");
});

test("incomplete applicant and travel details can be submitted and lower readiness", async () => {
  global.__demoStore = {
    customers: [],
    applications: [],
    visaTypes: [
      {
        _id: "visa-type-incomplete",
        active: true,
        destination: "Japan",
        category: "Tourist",
        countryId: "country-incomplete",
        countryCode: "JP",
        visaTypeCode: "VISA-JP-TEST",
      },
    ],
    requirements: [{
      ...requirement,
      visaTypeId: "visa-type-incomplete",
      weight: 10,
    }],
    documents: [],
    verifications: [],
  };
  const customerResponse = response();
  await createCustomer(
    {
      body: { fullName: "Example Applicant", allowIncomplete: true },
    },
    customerResponse,
  );
  assert.equal(customerResponse.statusCode, 201);

  const applicationResponse = response();
  await createApplication(
    {
      body: {
        customer: customerResponse.body.customer._id,
        visaType: "visa-type-incomplete",
      },
      user: { role: "ADMIN" },
    },
    applicationResponse,
  );
  assert.equal(applicationResponse.statusCode, 201);

  const verificationResponse = response();
  await runVerification(
    { body: { applicationId: applicationResponse.body.application._id } },
    verificationResponse,
  );
  assert.equal(verificationResponse.statusCode, 200);
  assert.equal(verificationResponse.body.verification.status, "MISSING");
  assert.equal(verificationResponse.body.verification.completenessScore, 10);
  assert.equal(
    verificationResponse.body.verification.results.find(
      (item) => item.requirementId === "form:email",
    ).status,
    "MISSING",
  );
  assert.equal(
    verificationResponse.body.verification.results.find(
      (item) => item.requirementId === "form:travelStartDate",
    ).status,
    "MISSING",
  );
  assert.equal(
    verificationResponse.body.verification.results.find(
      (item) => item.requirementId === requirement.requirementId,
    ).status,
    "MISSING",
  );
});
