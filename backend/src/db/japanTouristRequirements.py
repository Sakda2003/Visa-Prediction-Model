JAPAN_TOURIST_VISA_CODE = "VISA-JP-TOU-SIN-V90D-15D-S-KHM"

JAPAN_TOURIST_REQUIREMENTS = [
    {"code": "passport", "name": "Passport Copy", "required": True, "weight": 25},
    {"code": "bank_statement", "name": "Bank Statement (Last 3 Months)", "required": True, "weight": 40},
]


def japan_tourist_requirement_for_seed() -> dict:
    return {"code": JAPAN_TOURIST_VISA_CODE, "requirements": JAPAN_TOURIST_REQUIREMENTS}
