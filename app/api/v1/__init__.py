from fastapi import APIRouter

from app.api.v1.analyses import router as analyses_router
from app.api.v1.appointments import router as appointments_router
from app.api.v1.assignments import router as assignments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.billing import router as billing_router
from app.api.v1.clinics import router as clinics_router
from app.api.v1.encounters import router as encounters_router
from app.api.v1.medical_records import router as medical_records_router
from app.api.v1.patients import router as patients_router
from app.api.v1.relatives import router as relatives_router
from app.api.v1.reports import router as reports_router
from app.api.v1.schedules import router as schedules_router
from app.api.v1.users import router as users_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(clinics_router)
api_v1_router.include_router(patients_router)
api_v1_router.include_router(relatives_router)
api_v1_router.include_router(schedules_router)
api_v1_router.include_router(appointments_router)
api_v1_router.include_router(medical_records_router)
api_v1_router.include_router(encounters_router)
api_v1_router.include_router(assignments_router)
api_v1_router.include_router(analyses_router)
api_v1_router.include_router(billing_router)
api_v1_router.include_router(reports_router)
