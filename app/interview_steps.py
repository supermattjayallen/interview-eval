from enum import Enum


class InterviewStep(str, Enum):
    RECRUITER_SCREEN = "recruiter_screen"
    HIRING_MANAGER = "hiring_manager"
    TECHNICAL = "technical"
    CODING = "coding"
    SYSTEM_DESIGN = "system_design"
    BEHAVIORAL = "behavioral"
    CULTURE_FIT = "culture_fit"
    PANEL = "panel"
    FINAL = "final"
    OTHER = "other"


INTERVIEW_STEP_LABELS = {
    InterviewStep.RECRUITER_SCREEN: "Recruiter / phone screen",
    InterviewStep.HIRING_MANAGER: "Hiring manager",
    InterviewStep.TECHNICAL: "Technical interview",
    InterviewStep.CODING: "Coding interview",
    InterviewStep.SYSTEM_DESIGN: "System design",
    InterviewStep.BEHAVIORAL: "Behavioral",
    InterviewStep.CULTURE_FIT: "Culture fit",
    InterviewStep.PANEL: "Panel interview",
    InterviewStep.FINAL: "Final round",
    InterviewStep.OTHER: "Other",
}
