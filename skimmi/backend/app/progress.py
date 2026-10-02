"""Quiz scoring and the study progress summary.

A quiz message's content is JSON: {"questions": [...]}. The student's answers are
the index of the option they chose for each question. Only multiple-choice
questions are scored; quizzes saved before scoring existed may also contain
short-answer questions, which are left out of the score.
"""

import json

from . import config, database


class ScoringError(ValueError):
    """The answers don't fit the quiz."""


def quiz_questions(message):
    """The questions of a saved quiz message, or None if it isn't one."""
    if message["role"] != "assistant" or message["mode"] != "quiz":
        return None
    try:
        questions = json.loads(message["content"]).get("questions")
    except (ValueError, AttributeError):
        return None
    return questions if isinstance(questions, list) and questions else None


def score_quiz(questions, answers):
    """Score one attempt. `answers` has one entry per question: the chosen option's
    index for a multiple-choice question, None for any other.

    Returns {"results": [True/False/None per question], "correct": n, "total": n}.
    Raises ScoringError if an answer is missing or out of range.
    """
    if len(answers) != len(questions):
        raise ScoringError(f"Expected {len(questions)} answers, got {len(answers)}.")

    results = []
    for number, (question, answer) in enumerate(zip(questions, answers), 1):
        options = question.get("options") or []
        if question.get("type") != "multiple_choice" or not options:
            results.append(None)
            continue
        if not isinstance(answer, int) or isinstance(answer, bool) or not 0 <= answer < len(options):
            raise ScoringError(f"Question {number} needs an answer.")
        results.append(options[answer] == question["answer"])

    scored = [r for r in results if r is not None]
    if not scored:
        raise ScoringError("This quiz has no multiple-choice questions to score.")
    return {"results": results, "correct": sum(scored), "total": len(scored)}


def accuracy(correct, total):
    """Share of answers that were right, from 0 to 1. None before any answers."""
    return round(correct / total, 4) if total else None


def summary(user_id):
    """What the progress card shows, for one user."""
    quizzes, answered, correct = database.quiz_totals(user_id)
    activity = database.study_activity(user_id)
    return {
        "quizzes_completed": quizzes,
        "questions_answered": answered,
        "correct_answers": correct,
        "accuracy": accuracy(correct, answered),
        "activity": {mode: activity.get(mode, 0) for mode in config.MODES},
        "recent": database.recent_quiz_attempts(user_id, config.RECENT_QUIZZES),
    }
