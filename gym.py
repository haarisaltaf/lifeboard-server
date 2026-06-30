"""Gym / weightlifting engine: exercise library seed, muscle model, prebuilt
programs + a split recommender, and the analytics (estimated 1RM, weekly muscle
volume + balance, recovery, personal records, and smart recommendations).

Pure-ish: every function takes a sqlite connection and returns plain dicts.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- muscle model
# Canonical muscle list used across the library, volume targets, and heatmap.
# Each: weekly working-set target range (low, high) from hypertrophy guidance.
MUSCLES = {
    "Chest":       (10, 20),
    "Front Delts": (6, 12),
    "Side Delts":  (8, 18),
    "Rear Delts":  (6, 16),
    "Lats":        (10, 20),
    "Upper Back":  (10, 20),
    "Traps":       (6, 14),
    "Lower Back":  (6, 12),
    "Biceps":      (8, 16),
    "Triceps":     (8, 16),
    "Forearms":    (4, 12),
    "Quads":       (10, 20),
    "Hamstrings":  (8, 16),
    "Glutes":      (8, 16),
    "Calves":      (8, 16),
    "Abs":         (8, 16),
    "Obliques":    (4, 12),
}
MUSCLE_NAMES = list(MUSCLES.keys())

EQUIPMENT = ["Barbell", "Dumbbell", "Machine", "Cable", "Bodyweight",
             "Smith Machine", "EZ Bar", "Kettlebell", "Band"]


# ---------------------------------------------------------------- exercise seed
# (name, equipment, primary[], secondary[], category, pattern, cue, alts[])
def _ex(name, equip, prim, sec, cat, pat, cue, alts):
    return {"name": name, "equipment": equip, "primary": prim, "secondary": sec,
            "category": cat, "pattern": pat, "cue": cue, "alts": alts}


EXERCISES = [
    # ---- chest
    _ex("Barbell Bench Press", "Barbell", ["Chest"], ["Front Delts", "Triceps"], "compound", "push",
        "Retract shoulder blades, touch mid-chest, drive feet.", ["Dumbbell Bench Press", "Machine Chest Press", "Incline Barbell Bench Press", "Push-up"]),
    _ex("Incline Barbell Bench Press", "Barbell", ["Chest", "Front Delts"], ["Triceps"], "compound", "push",
        "~30° bench, bar to upper chest.", ["Incline Dumbbell Press", "Barbell Bench Press"]),
    _ex("Dumbbell Bench Press", "Dumbbell", ["Chest"], ["Front Delts", "Triceps"], "compound", "push",
        "Deep stretch at the bottom, press to lockout.", ["Barbell Bench Press", "Machine Chest Press"]),
    _ex("Incline Dumbbell Press", "Dumbbell", ["Chest", "Front Delts"], ["Triceps"], "compound", "push",
        "Control the stretch, don't clank the dumbbells.", ["Incline Barbell Bench Press", "Dumbbell Bench Press"]),
    _ex("Machine Chest Press", "Machine", ["Chest"], ["Front Delts", "Triceps"], "compound", "push",
        "Seat height so handles sit at mid-chest.", ["Barbell Bench Press", "Dumbbell Bench Press"]),
    _ex("Cable Fly", "Cable", ["Chest"], ["Front Delts"], "isolation", "push",
        "Slight elbow bend, squeeze across the midline.", ["Pec Deck", "Dumbbell Fly"]),
    _ex("Pec Deck", "Machine", ["Chest"], [], "isolation", "push",
        "Drive elbows together, controlled negative.", ["Cable Fly"]),
    _ex("Push-up", "Bodyweight", ["Chest"], ["Front Delts", "Triceps"], "compound", "push",
        "Rigid plank, full lockout.", ["Machine Chest Press", "Dips"]),
    _ex("Dips", "Bodyweight", ["Chest", "Triceps"], ["Front Delts"], "compound", "push",
        "Lean forward for chest, upright for triceps.", ["Machine Chest Press", "Close-Grip Bench Press"]),
    # ---- back
    _ex("Deadlift", "Barbell", ["Lower Back", "Glutes", "Hamstrings"], ["Lats", "Traps", "Quads"], "compound", "pull",
        "Neutral spine, push the floor away.", ["Trap Bar Deadlift", "Romanian Deadlift"]),
    _ex("Trap Bar Deadlift", "Barbell", ["Quads", "Glutes"], ["Hamstrings", "Lower Back", "Traps"], "compound", "pull",
        "More quad-driven than a conventional pull.", ["Deadlift"]),
    _ex("Pull-up", "Bodyweight", ["Lats"], ["Biceps", "Upper Back"], "compound", "pull",
        "Full hang to chin over bar, drive elbows down.", ["Lat Pulldown", "Assisted Pull-up"]),
    _ex("Assisted Pull-up", "Machine", ["Lats"], ["Biceps", "Upper Back"], "compound", "pull",
        "Use the lightest assistance you can.", ["Pull-up", "Lat Pulldown"]),
    _ex("Lat Pulldown", "Cable", ["Lats"], ["Biceps", "Upper Back"], "compound", "pull",
        "Pull to upper chest, no excessive lean.", ["Pull-up"]),
    _ex("Barbell Row", "Barbell", ["Upper Back", "Lats"], ["Biceps", "Lower Back"], "compound", "pull",
        "Hinge ~45°, pull to lower ribs.", ["Dumbbell Row", "Seated Cable Row", "T-Bar Row"]),
    _ex("Dumbbell Row", "Dumbbell", ["Lats", "Upper Back"], ["Biceps"], "compound", "pull",
        "Brace on bench, drive elbow to hip.", ["Barbell Row", "Seated Cable Row"]),
    _ex("Seated Cable Row", "Cable", ["Upper Back", "Lats"], ["Biceps"], "compound", "pull",
        "Tall chest, squeeze shoulder blades.", ["Barbell Row", "Dumbbell Row"]),
    _ex("T-Bar Row", "Machine", ["Upper Back", "Lats"], ["Biceps"], "compound", "pull",
        "Chest supported if available.", ["Barbell Row"]),
    _ex("Face Pull", "Cable", ["Rear Delts", "Upper Back"], [], "isolation", "pull",
        "Pull to forehead, externally rotate.", ["Reverse Pec Deck"]),
    _ex("Reverse Pec Deck", "Machine", ["Rear Delts"], ["Upper Back"], "isolation", "pull",
        "Lead with the elbows, not the hands.", ["Face Pull"]),
    _ex("Romanian Deadlift", "Barbell", ["Hamstrings", "Glutes"], ["Lower Back"], "compound", "pull",
        "Push hips back, soft knees, feel the stretch.", ["Deadlift", "Leg Curl"]),
    # ---- shoulders
    _ex("Overhead Press", "Barbell", ["Front Delts"], ["Side Delts", "Triceps"], "compound", "push",
        "Brace glutes, bar over mid-foot at lockout.", ["Dumbbell Shoulder Press", "Machine Shoulder Press"]),
    _ex("Dumbbell Shoulder Press", "Dumbbell", ["Front Delts"], ["Side Delts", "Triceps"], "compound", "push",
        "Press slightly inward to lockout.", ["Overhead Press", "Machine Shoulder Press"]),
    _ex("Machine Shoulder Press", "Machine", ["Front Delts"], ["Side Delts", "Triceps"], "compound", "push",
        "Good when fatigued or training alone.", ["Overhead Press"]),
    _ex("Lateral Raise", "Dumbbell", ["Side Delts"], [], "isolation", "push",
        "Lead with elbows, no swinging.", ["Cable Lateral Raise"]),
    _ex("Cable Lateral Raise", "Cable", ["Side Delts"], [], "isolation", "push",
        "Constant tension across the whole range.", ["Lateral Raise"]),
    # ---- arms
    _ex("Barbell Curl", "Barbell", ["Biceps"], ["Forearms"], "isolation", "pull",
        "Pin elbows, no hip swing.", ["Dumbbell Curl", "EZ Bar Curl", "Cable Curl"]),
    _ex("Dumbbell Curl", "Dumbbell", ["Biceps"], ["Forearms"], "isolation", "pull",
        "Supinate as you curl.", ["Barbell Curl", "Hammer Curl"]),
    _ex("Hammer Curl", "Dumbbell", ["Biceps", "Forearms"], [], "isolation", "pull",
        "Neutral grip, hits the brachialis.", ["Dumbbell Curl"]),
    _ex("EZ Bar Curl", "EZ Bar", ["Biceps"], ["Forearms"], "isolation", "pull",
        "Easier on the wrists than a straight bar.", ["Barbell Curl"]),
    _ex("Cable Curl", "Cable", ["Biceps"], [], "isolation", "pull",
        "Constant tension, slow negative.", ["Barbell Curl"]),
    _ex("Triceps Pushdown", "Cable", ["Triceps"], [], "isolation", "push",
        "Pin elbows, full lockout.", ["Overhead Triceps Extension", "Skull Crusher"]),
    _ex("Overhead Triceps Extension", "Dumbbell", ["Triceps"], [], "isolation", "push",
        "Deep stretch overhead, long-head focus.", ["Triceps Pushdown"]),
    _ex("Skull Crusher", "EZ Bar", ["Triceps"], [], "isolation", "push",
        "Lower to forehead/behind head, elbows still.", ["Triceps Pushdown"]),
    _ex("Close-Grip Bench Press", "Barbell", ["Triceps", "Chest"], ["Front Delts"], "compound", "push",
        "Shoulder-width grip, tuck elbows.", ["Dips", "Triceps Pushdown"]),
    # ---- legs
    _ex("Back Squat", "Barbell", ["Quads", "Glutes"], ["Hamstrings", "Lower Back"], "compound", "legs",
        "Brace, break at hips and knees, depth to parallel+.", ["Front Squat", "Leg Press", "Hack Squat"]),
    _ex("Front Squat", "Barbell", ["Quads"], ["Glutes", "Abs"], "compound", "legs",
        "Elbows high, upright torso.", ["Back Squat"]),
    _ex("Leg Press", "Machine", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Don't round the lower back at depth.", ["Back Squat", "Hack Squat"]),
    _ex("Hack Squat", "Machine", ["Quads"], ["Glutes"], "compound", "legs",
        "Feet low for quads, deep range.", ["Leg Press", "Back Squat"]),
    _ex("Leg Extension", "Machine", ["Quads"], [], "isolation", "legs",
        "Pause and squeeze at the top.", ["Hack Squat"]),
    _ex("Leg Curl", "Machine", ["Hamstrings"], [], "isolation", "legs",
        "Control the negative, full range.", ["Romanian Deadlift"]),
    _ex("Walking Lunge", "Dumbbell", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Long stride for glutes, short for quads.", ["Bulgarian Split Squat"]),
    _ex("Bulgarian Split Squat", "Dumbbell", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Rear foot elevated, drive through front heel.", ["Walking Lunge"]),
    _ex("Hip Thrust", "Barbell", ["Glutes"], ["Hamstrings"], "compound", "legs",
        "Chin tucked, full hip extension, squeeze.", ["Romanian Deadlift"]),
    _ex("Standing Calf Raise", "Machine", ["Calves"], [], "isolation", "legs",
        "Full stretch at the bottom, pause at top.", ["Seated Calf Raise"]),
    _ex("Seated Calf Raise", "Machine", ["Calves"], [], "isolation", "legs",
        "Targets the soleus; slow tempo.", ["Standing Calf Raise"]),
    # ---- core
    _ex("Plank", "Bodyweight", ["Abs"], ["Obliques"], "isolation", "core",
        "Squeeze glutes, ribs down, don't sag.", ["Hanging Leg Raise"]),
    _ex("Hanging Leg Raise", "Bodyweight", ["Abs"], ["Obliques"], "isolation", "core",
        "Posterior pelvic tilt, control the swing.", ["Cable Crunch"]),
    _ex("Cable Crunch", "Cable", ["Abs"], [], "isolation", "core",
        "Crunch the ribs to pelvis, hips fixed.", ["Hanging Leg Raise"]),
]

# --- expanded library (v2.7): broader coverage across equipment & variations ---
EXERCISES += [
    # chest
    _ex("Decline Barbell Bench Press", "Barbell", ["Chest"], ["Triceps", "Front Delts"], "compound", "push",
        "Lower-chest bias; controlled descent.", ["Barbell Bench Press", "Dips"]),
    _ex("Decline Dumbbell Press", "Dumbbell", ["Chest"], ["Triceps", "Front Delts"], "compound", "push",
        "Lower-chest focus with a deep stretch.", ["Decline Barbell Bench Press"]),
    _ex("Smith Machine Bench Press", "Smith Machine", ["Chest"], ["Triceps", "Front Delts"], "compound", "push",
        "Fixed bar path; good for pushing near failure.", ["Barbell Bench Press"]),
    _ex("Incline Smith Machine Press", "Smith Machine", ["Chest", "Front Delts"], ["Triceps"], "compound", "push",
        "Upper-chest with a stable path.", ["Incline Barbell Bench Press"]),
    _ex("Dumbbell Fly", "Dumbbell", ["Chest"], ["Front Delts"], "isolation", "push",
        "Big stretch, hug the dumbbells together.", ["Cable Fly", "Pec Deck"]),
    _ex("Incline Dumbbell Fly", "Dumbbell", ["Chest", "Front Delts"], [], "isolation", "push",
        "Upper-chest stretch; soft elbows.", ["Cable Fly"]),
    _ex("Cable Crossover", "Cable", ["Chest"], ["Front Delts"], "isolation", "push",
        "High-to-low; cross the hands at the bottom.", ["Cable Fly", "Pec Deck"]),
    _ex("Low-to-High Cable Fly", "Cable", ["Chest"], ["Front Delts"], "isolation", "push",
        "Upper-chest emphasis; sweep up and in.", ["Cable Crossover"]),
    _ex("Incline Machine Press", "Machine", ["Chest", "Front Delts"], ["Triceps"], "compound", "push",
        "Upper-chest with a fixed path.", ["Incline Barbell Bench Press", "Machine Chest Press"]),
    _ex("Incline Push-up", "Bodyweight", ["Chest"], ["Front Delts", "Triceps"], "compound", "push",
        "Hands elevated; an easier regression.", ["Push-up"]),
    _ex("Decline Push-up", "Bodyweight", ["Chest", "Front Delts"], ["Triceps"], "compound", "push",
        "Feet elevated; upper-chest progression.", ["Push-up"]),
    _ex("Diamond Push-up", "Bodyweight", ["Triceps", "Chest"], ["Front Delts"], "compound", "push",
        "Hands together; triceps focus.", ["Close-Grip Bench Press", "Push-up"]),
    _ex("Landmine Press", "Barbell", ["Front Delts", "Chest"], ["Triceps"], "compound", "push",
        "Press up and in along the bar's arc.", ["Overhead Press"]),
    # back
    _ex("Sumo Deadlift", "Barbell", ["Glutes", "Quads"], ["Hamstrings", "Lower Back", "Traps"], "compound", "pull",
        "Wide stance, hips closer to the bar.", ["Deadlift"]),
    _ex("Pendlay Row", "Barbell", ["Upper Back", "Lats"], ["Biceps", "Lower Back"], "compound", "pull",
        "Dead-stop from the floor each rep, explosive.", ["Barbell Row"]),
    _ex("Chest-Supported Row", "Machine", ["Upper Back", "Lats"], ["Biceps", "Rear Delts"], "compound", "pull",
        "Chest pinned; removes lower-back fatigue.", ["Seated Cable Row", "Barbell Row"]),
    _ex("Meadows Row", "Barbell", ["Lats", "Upper Back"], ["Biceps", "Rear Delts"], "compound", "pull",
        "Landmine, single-arm, big stretch.", ["Dumbbell Row"]),
    _ex("Machine Row", "Machine", ["Upper Back", "Lats"], ["Biceps"], "compound", "pull",
        "Squeeze the blades, controlled return.", ["Seated Cable Row"]),
    _ex("Chin-up", "Bodyweight", ["Lats", "Biceps"], ["Upper Back"], "compound", "pull",
        "Supinated grip; more biceps than a pull-up.", ["Pull-up", "Lat Pulldown"]),
    _ex("Neutral-Grip Pull-up", "Bodyweight", ["Lats"], ["Biceps", "Upper Back"], "compound", "pull",
        "Palms facing; joint-friendly.", ["Pull-up"]),
    _ex("Wide-Grip Lat Pulldown", "Cable", ["Lats"], ["Upper Back", "Biceps"], "compound", "pull",
        "Wide grip, drive elbows down and back.", ["Lat Pulldown"]),
    _ex("Close-Grip Lat Pulldown", "Cable", ["Lats"], ["Biceps", "Upper Back"], "compound", "pull",
        "Neutral close grip, big stretch up top.", ["Lat Pulldown"]),
    _ex("Single-Arm Lat Pulldown", "Cable", ["Lats"], ["Biceps"], "isolation", "pull",
        "One arm to chase a full stretch + contraction.", ["Lat Pulldown"]),
    _ex("Straight-Arm Pulldown", "Cable", ["Lats"], [], "isolation", "pull",
        "Arms straight, pull the bar to your thighs.", ["Dumbbell Pullover"]),
    _ex("Dumbbell Pullover", "Dumbbell", ["Lats", "Chest"], [], "isolation", "pull",
        "Stretch overhead, pull back over the chest.", ["Straight-Arm Pulldown"]),
    _ex("Inverted Row", "Bodyweight", ["Upper Back", "Lats"], ["Biceps"], "compound", "pull",
        "Body rigid, pull the chest to the bar.", ["Seated Cable Row"]),
    _ex("Rack Pull", "Barbell", ["Upper Back", "Traps", "Lower Back"], ["Glutes", "Lats"], "compound", "pull",
        "Partial deadlift from knee height; heavy.", ["Deadlift"]),
    _ex("Good Morning", "Barbell", ["Hamstrings", "Lower Back"], ["Glutes"], "compound", "pull",
        "Hinge with a braced spine; start light.", ["Romanian Deadlift"]),
    _ex("Back Extension", "Bodyweight", ["Lower Back", "Glutes"], ["Hamstrings"], "isolation", "pull",
        "Extend to neutral; no jerking.", ["Romanian Deadlift"]),
    _ex("Barbell Shrug", "Barbell", ["Traps"], ["Forearms"], "isolation", "pull",
        "Shrug straight up, pause, no rolling.", ["Dumbbell Shrug"]),
    _ex("Dumbbell Shrug", "Dumbbell", ["Traps"], ["Forearms"], "isolation", "pull",
        "Full elevation, slow negative.", ["Barbell Shrug"]),
    _ex("Rear Delt Fly", "Dumbbell", ["Rear Delts"], ["Upper Back"], "isolation", "pull",
        "Hinge over, raise to the sides, pinkies up.", ["Reverse Pec Deck", "Face Pull"]),
    _ex("Cable Rear Delt Fly", "Cable", ["Rear Delts"], ["Upper Back"], "isolation", "pull",
        "Cross the cables, lead with the elbows.", ["Reverse Pec Deck"]),
    # shoulders
    _ex("Arnold Press", "Dumbbell", ["Front Delts"], ["Side Delts", "Triceps"], "compound", "push",
        "Rotate from palms-in to palms-out.", ["Dumbbell Shoulder Press"]),
    _ex("Seated Dumbbell Shoulder Press", "Dumbbell", ["Front Delts"], ["Side Delts", "Triceps"], "compound", "push",
        "Back supported; press to lockout.", ["Overhead Press", "Arnold Press"]),
    _ex("Push Press", "Barbell", ["Front Delts"], ["Side Delts", "Triceps", "Quads"], "compound", "push",
        "Leg drive to move heavier loads.", ["Overhead Press"]),
    _ex("Smith Machine Shoulder Press", "Smith Machine", ["Front Delts"], ["Side Delts", "Triceps"], "compound", "push",
        "Fixed path; good for failure work.", ["Overhead Press"]),
    _ex("Dumbbell Front Raise", "Dumbbell", ["Front Delts"], [], "isolation", "push",
        "Raise to eye level, no swing.", ["Cable Front Raise"]),
    _ex("Cable Front Raise", "Cable", ["Front Delts"], [], "isolation", "push",
        "Constant tension to the front.", ["Dumbbell Front Raise"]),
    _ex("Machine Lateral Raise", "Machine", ["Side Delts"], [], "isolation", "push",
        "Pad against the upper arm; clean reps.", ["Lateral Raise"]),
    _ex("Upright Row", "Barbell", ["Side Delts", "Traps"], ["Biceps"], "compound", "pull",
        "Pull to chest height, elbows lead.", ["Cable Upright Row", "Lateral Raise"]),
    _ex("Cable Upright Row", "Cable", ["Side Delts", "Traps"], ["Biceps"], "compound", "pull",
        "Rope or bar, elbows high.", ["Upright Row"]),
    # biceps
    _ex("Preacher Curl", "EZ Bar", ["Biceps"], ["Forearms"], "isolation", "pull",
        "Arms locked on the pad; no cheating.", ["Barbell Curl", "Machine Preacher Curl"]),
    _ex("Machine Preacher Curl", "Machine", ["Biceps"], [], "isolation", "pull",
        "Fixed path, big squeeze.", ["Preacher Curl"]),
    _ex("Incline Dumbbell Curl", "Dumbbell", ["Biceps"], ["Forearms"], "isolation", "pull",
        "Arms behind the torso for max stretch.", ["Dumbbell Curl"]),
    _ex("Concentration Curl", "Dumbbell", ["Biceps"], [], "isolation", "pull",
        "Elbow on the thigh, peak contraction.", ["Dumbbell Curl"]),
    _ex("Spider Curl", "EZ Bar", ["Biceps"], [], "isolation", "pull",
        "Chest on an incline bench, strict.", ["Preacher Curl"]),
    _ex("Cable Rope Hammer Curl", "Cable", ["Biceps", "Forearms"], [], "isolation", "pull",
        "Neutral rope grip, constant tension.", ["Hammer Curl"]),
    _ex("Reverse Curl", "EZ Bar", ["Forearms", "Biceps"], [], "isolation", "pull",
        "Pronated grip; hits the brachioradialis.", ["Hammer Curl"]),
    # triceps
    _ex("Rope Triceps Pushdown", "Cable", ["Triceps"], [], "isolation", "push",
        "Spread the rope apart at lockout.", ["Triceps Pushdown"]),
    _ex("Overhead Cable Extension", "Cable", ["Triceps"], [], "isolation", "push",
        "Long-head stretch overhead.", ["Overhead Triceps Extension"]),
    _ex("Triceps Kickback", "Dumbbell", ["Triceps"], [], "isolation", "push",
        "Upper arm still, full lockout.", ["Triceps Pushdown"]),
    _ex("Bench Dip", "Bodyweight", ["Triceps"], ["Chest", "Front Delts"], "compound", "push",
        "Hips close to the bench, controlled.", ["Dips"]),
    _ex("Dumbbell Skull Crusher", "Dumbbell", ["Triceps"], [], "isolation", "push",
        "Lower beside the head, elbows fixed.", ["Skull Crusher"]),
    _ex("JM Press", "Barbell", ["Triceps"], ["Chest"], "compound", "push",
        "Hybrid of close-grip press and skull crusher.", ["Close-Grip Bench Press"]),
    # forearms
    _ex("Wrist Curl", "Dumbbell", ["Forearms"], [], "isolation", "pull",
        "Flex the wrists over a bench edge.", ["Reverse Wrist Curl"]),
    _ex("Reverse Wrist Curl", "Dumbbell", ["Forearms"], [], "isolation", "pull",
        "Extend the wrists; use lighter loads.", ["Wrist Curl"]),
    _ex("Farmer's Carry", "Dumbbell", ["Forearms", "Traps"], ["Abs"], "compound", "pull",
        "Walk tall and braced; crush the handles.", ["Dumbbell Shrug"]),
    # quads / legs
    _ex("Smith Machine Squat", "Smith Machine", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Fixed path; feet slightly forward.", ["Back Squat"]),
    _ex("Goblet Squat", "Dumbbell", ["Quads", "Glutes"], ["Abs"], "compound", "legs",
        "Hold at the chest, sit between the hips.", ["Back Squat", "Front Squat"]),
    _ex("Pendulum Squat", "Machine", ["Quads"], ["Glutes"], "compound", "legs",
        "Deep, knee-forward quad bias.", ["Hack Squat"]),
    _ex("Box Squat", "Barbell", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Sit back to a box, stay tight.", ["Back Squat"]),
    _ex("Step-up", "Dumbbell", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Drive through the top foot, control down.", ["Bulgarian Split Squat", "Walking Lunge"]),
    _ex("Reverse Lunge", "Dumbbell", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Step back; easier on the knees.", ["Walking Lunge"]),
    _ex("Sissy Squat", "Bodyweight", ["Quads"], [], "isolation", "legs",
        "Knees forward, lean back; deep quad stretch.", ["Leg Extension"]),
    _ex("Belt Squat", "Machine", ["Quads", "Glutes"], ["Hamstrings"], "compound", "legs",
        "Loads the hips, spares the spine.", ["Hack Squat", "Leg Press"]),
    # hamstrings
    _ex("Lying Leg Curl", "Machine", ["Hamstrings"], ["Calves"], "isolation", "legs",
        "Full range, squeeze at the top.", ["Leg Curl", "Seated Leg Curl"]),
    _ex("Seated Leg Curl", "Machine", ["Hamstrings"], [], "isolation", "legs",
        "Hips flexed; great hamstring stretch.", ["Lying Leg Curl"]),
    _ex("Nordic Curl", "Bodyweight", ["Hamstrings"], ["Glutes"], "isolation", "legs",
        "Lower slowly; brutal eccentric.", ["Lying Leg Curl"]),
    _ex("Stiff-Leg Deadlift", "Barbell", ["Hamstrings", "Glutes"], ["Lower Back"], "compound", "legs",
        "Minimal knee bend, hinge from the hips.", ["Romanian Deadlift"]),
    _ex("Single-Leg Romanian Deadlift", "Dumbbell", ["Hamstrings", "Glutes"], ["Lower Back"], "compound", "legs",
        "Hinge on one leg; balance and stretch.", ["Romanian Deadlift"]),
    _ex("Glute-Ham Raise", "Bodyweight", ["Hamstrings"], ["Glutes", "Calves"], "compound", "legs",
        "Control the descent, pull yourself up.", ["Nordic Curl", "Lying Leg Curl"]),
    # glutes
    _ex("Barbell Hip Thrust", "Barbell", ["Glutes"], ["Hamstrings"], "compound", "legs",
        "Full lockout, ribs down, squeeze hard.", ["Hip Thrust"]),
    _ex("Single-Leg Hip Thrust", "Bodyweight", ["Glutes"], ["Hamstrings"], "compound", "legs",
        "One leg; chase a hard contraction.", ["Hip Thrust"]),
    _ex("Cable Pull-Through", "Cable", ["Glutes"], ["Hamstrings"], "isolation", "legs",
        "Hinge, then snap the hips forward.", ["Romanian Deadlift", "Hip Thrust"]),
    _ex("Glute Kickback", "Cable", ["Glutes"], ["Hamstrings"], "isolation", "legs",
        "Drive the heel back and up.", ["Hip Thrust"]),
    _ex("Hip Abduction", "Machine", ["Glutes"], [], "isolation", "legs",
        "Push the knees out, pause at the end.", ["Glute Kickback"]),
    # calves
    _ex("Leg Press Calf Raise", "Machine", ["Calves"], [], "isolation", "legs",
        "Full stretch and squeeze on the sled.", ["Standing Calf Raise"]),
    _ex("Donkey Calf Raise", "Machine", ["Calves"], [], "isolation", "legs",
        "Hips hinged; big stretch.", ["Standing Calf Raise"]),
    _ex("Smith Machine Calf Raise", "Smith Machine", ["Calves"], [], "isolation", "legs",
        "Balls of the feet on a plate, controlled.", ["Standing Calf Raise"]),
    # core
    _ex("Crunch", "Bodyweight", ["Abs"], [], "isolation", "core",
        "Curl the ribs down; don't yank the neck.", ["Cable Crunch"]),
    _ex("Sit-up", "Bodyweight", ["Abs"], ["Obliques"], "isolation", "core",
        "Full range; control the descent.", ["Crunch"]),
    _ex("Bicycle Crunch", "Bodyweight", ["Abs", "Obliques"], [], "isolation", "core",
        "Opposite elbow to knee, slow.", ["Russian Twist"]),
    _ex("Russian Twist", "Bodyweight", ["Obliques"], ["Abs"], "isolation", "core",
        "Rotate side to side, chest tall.", ["Cable Woodchopper"]),
    _ex("Cable Woodchopper", "Cable", ["Obliques"], ["Abs"], "isolation", "core",
        "Rotate through the trunk, hips still.", ["Russian Twist", "Pallof Press"]),
    _ex("Pallof Press", "Cable", ["Obliques"], ["Abs"], "isolation", "core",
        "Resist rotation; brace and press out.", ["Cable Woodchopper"]),
    _ex("Ab Wheel Rollout", "Bodyweight", ["Abs"], ["Obliques"], "isolation", "core",
        "Brace hard; only go as far as you control.", ["Plank"]),
    _ex("Lying Leg Raise", "Bodyweight", ["Abs"], ["Obliques"], "isolation", "core",
        "Lower the legs slowly, back flat.", ["Hanging Leg Raise"]),
    _ex("Side Plank", "Bodyweight", ["Obliques"], ["Abs"], "isolation", "core",
        "Straight line, hips high.", ["Plank"]),
    _ex("Mountain Climbers", "Bodyweight", ["Abs"], ["Obliques"], "isolation", "core",
        "Drive the knees, keep the hips low.", ["Plank"]),
    # full body / conditioning
    _ex("Kettlebell Swing", "Kettlebell", ["Glutes", "Hamstrings"], ["Lower Back", "Front Delts"], "compound", "legs",
        "Hip snap, not a squat; arms are ropes.", ["Romanian Deadlift", "Cable Pull-Through"]),
    _ex("Thruster", "Barbell", ["Quads", "Front Delts"], ["Glutes", "Triceps"], "compound", "legs",
        "Front squat straight into an overhead press.", ["Push Press", "Front Squat"]),
    _ex("Power Clean", "Barbell", ["Traps", "Glutes", "Quads"], ["Hamstrings", "Upper Back"], "compound", "pull",
        "Explosive triple extension; catch in a quarter squat.", ["Deadlift"]),
    _ex("Clean and Press", "Barbell", ["Front Delts", "Traps"], ["Quads", "Glutes", "Triceps"], "compound", "push",
        "Clean to the shoulders, then press overhead.", ["Power Clean", "Push Press"]),
    _ex("Burpee", "Bodyweight", ["Quads", "Chest"], ["Front Delts", "Abs"], "compound", "legs",
        "Chest to the floor, jump at the top.", ["Push-up"]),
]


def seed(conn):
    """Insert any built-in exercises not already present (idempotent, and adds
    newly-shipped exercises to databases seeded by an older version)."""
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM gym_exercises WHERE is_custom=0").fetchall()}
    ts = _now()
    for e in EXERCISES:
        if e["name"] in existing:
            continue
        conn.execute(
            "INSERT INTO gym_exercises(name, equipment, primary_m, secondary_m, category, pattern, cue, alts, is_custom, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,0,?)",
            (e["name"], e["equipment"], json.dumps(e["primary"]), json.dumps(e["secondary"]),
             e["category"], e["pattern"], e["cue"], json.dumps(e["alts"]), ts),
        )


def exercise_row(r):
    d = dict(r)
    d["primary"] = json.loads(d.pop("primary_m") or "[]")
    d["secondary"] = json.loads(d.pop("secondary_m") or "[]")
    d["alts"] = json.loads(d.get("alts") or "[]")
    d["is_custom"] = bool(d["is_custom"])
    stored = json.loads(d.pop("content", "{}") or "{}")
    d["content"] = content_for(d["name"], d.get("equipment", ""), d.get("cue", ""), d.get("category", ""), stored)
    return d


# ---------------------------------------------------------------- exercise content
# Curated, richer content for the marquee lifts; everything else gets sensible
# derived defaults (a video search link, grip options from equipment, etc.) and
# is fully editable per exercise.
CONTENT = {
    "Barbell Bench Press": {
        "instructions": ["Lie back, eyes under the bar, feet planted, slight arch.",
                          "Grip just outside shoulder width and pull the bar out of the rack.",
                          "Lower under control to the mid-chest, elbows ~45°.",
                          "Press back up and slightly toward your face to lockout."],
        "mistakes": ["Bouncing the bar off the chest", "Flaring the elbows to 90°", "Lifting the hips off the bench"],
        "tips": ["Squeeze the bar hard and 'bend' it to engage the lats", "Keep the shoulder blades retracted throughout"],
        "rom": "Bar touches the chest, full elbow lockout at the top.",
        "strength_curve": "Ascending — hardest off the chest, easier near lockout.",
    },
    "Back Squat": {
        "instructions": ["Bar on the upper traps, brace the core, unrack and step back.",
                          "Break at the hips and knees together, knees tracking over the toes.",
                          "Descend to at least parallel, then drive up through mid-foot."],
        "mistakes": ["Knees caving in", "Heels rising", "Rounding the lower back at depth", "Good-morning-ing out of the hole"],
        "tips": ["Spread the floor with your feet", "Big breath into the belly and brace before each rep"],
        "rom": "Hip crease to at least parallel with the knee.",
        "strength_curve": "Ascending — hardest at the bottom.",
    },
    "Deadlift": {
        "instructions": ["Bar over mid-foot, hinge and grip just outside the knees.",
                          "Drop the hips, chest up, lats tight, slack out of the bar.",
                          "Push the floor away and stand tall, hips and shoulders rising together.",
                          "Return by pushing the hips back, bar close to the legs."],
        "mistakes": ["Rounding the lower back", "Bar drifting away from the shins", "Hips shooting up first", "Jerking the bar"],
        "tips": ["Think 'push the floor', not 'pull the bar'", "Engage the lats — protect the bar to your body"],
        "rom": "Floor to full hip and knee lockout.",
        "strength_curve": "Roughly even, often hardest just off the floor.",
    },
    "Overhead Press": {
        "instructions": ["Bar on the front delts, grip just outside shoulders, elbows slightly in front.",
                          "Brace glutes and core, press the bar straight up, moving the head back then through.",
                          "Lock out with the bar over the mid-foot, shrug slightly at the top."],
        "mistakes": ["Excessive lower-back lean", "Pressing the bar forward", "Not finishing overhead"],
        "tips": ["Squeeze the glutes to stop the lean", "Get the head 'through the window' at lockout"],
        "rom": "Shoulders to full overhead lockout.",
        "strength_curve": "Ascending — hardest off the shoulders.",
    },
    "Barbell Row": {
        "instructions": ["Hinge to ~45°, neutral spine, bar hanging at arm's length.",
                          "Pull the bar to the lower ribs, driving the elbows back.",
                          "Squeeze the shoulder blades, lower under control."],
        "mistakes": ["Using too much body english", "Shrugging instead of rowing", "Standing too upright"],
        "tips": ["Lead with the elbows", "Keep the core braced to protect the lower back"],
        "rom": "Full stretch at the bottom to bar-to-ribs at the top.",
        "strength_curve": "Bell-shaped — hardest in the mid-range.",
    },
    "Pull-up": {
        "instructions": ["Hang from the bar, hands just outside shoulder width.",
                          "Pull the elbows down and back, chest to the bar.",
                          "Lower all the way to a full hang each rep."],
        "mistakes": ["Half reps / not reaching full hang", "Kipping when training for strength", "Shrugging the shoulders up"],
        "tips": ["Start by depressing the shoulder blades", "Drive the elbows toward your hips"],
        "rom": "Full dead hang to chin over the bar.",
        "strength_curve": "Ascending — hardest near the top.",
    },
    "Romanian Deadlift": {
        "instructions": ["Stand tall holding the bar, soft knees.",
                          "Push the hips back, bar sliding down the thighs, feel the hamstring stretch.",
                          "Drive the hips forward to stand, squeezing the glutes."],
        "mistakes": ["Bending the knees too much (turning it into a squat)", "Rounding the back", "Letting the bar drift forward"],
        "tips": ["Keep the bar against the legs", "Only go as low as you can keep a neutral spine"],
        "rom": "Until the hamstrings limit the stretch (usually mid-shin).",
        "strength_curve": "Hardest at the bottom (deep stretch).",
    },
    "Lat Pulldown": {
        "instructions": ["Secure the thighs, grip wider than shoulders.",
                          "Pull the bar to the upper chest, driving elbows down.",
                          "Control the bar back to a full stretch."],
        "mistakes": ["Leaning back excessively", "Pulling behind the neck", "Using momentum"],
        "tips": ["Think about pulling with the elbows, not the hands", "Pause briefly at the bottom"],
        "rom": "Full overhead stretch to bar-at-chest.",
        "strength_curve": "Ascending — hardest near the chest.",
    },
}


def _grip_for(equipment):
    return {
        "Barbell": ["Overhand", "Underhand", "Mixed"],
        "EZ Bar": ["Angled (wrist-friendly)", "Wide", "Close"],
        "Dumbbell": ["Neutral", "Pronated", "Supinated"],
        "Cable": ["Rope", "Straight bar", "Single handle", "Wide"],
        "Machine": ["Machine handles"],
        "Smith Machine": ["Overhand"],
        "Kettlebell": ["Neutral"],
        "Band": ["Neutral"],
        "Bodyweight": ["Standard", "Wide", "Close", "Neutral"],
    }.get(equipment, ["Standard"])


def _curve_for(category, equipment):
    if equipment == "Cable":
        return "Even — constant tension through the range."
    if category == "isolation":
        return "Bell-shaped — hardest in the mid-range."
    return "Ascending — hardest near the top/lockout."


def content_for(name, equipment, cue, category, stored):
    c = stored or {}
    cur = CONTENT.get(name, {})
    pick = lambda k, d: (c.get(k) if c.get(k) not in (None, "", []) else None) or cur.get(k) or d
    return {
        "video": pick("video", "https://www.youtube.com/results?search_query=" + urllib.parse.quote(name + " exercise form")),
        "instructions": pick("instructions", [cue] if cue else []),
        "mistakes": pick("mistakes", []),
        "tips": pick("tips", []),
        "rom": pick("rom", "Move through a full range — controlled stretch into a strong contraction."),
        "grip": pick("grip", _grip_for(equipment)),
        "strength_curve": pick("strength_curve", _curve_for(category, equipment)),
    }


# ---------------------------------------------------------------- programs
def _it(ex, sets, reps, rir="1-2"):
    return {"exercise": ex, "sets": sets, "reps": reps, "rir": rir}


PROGRAMS = [
    {
        "id": "fullbody", "name": "Full Body (3-day)", "days_per_week": 3, "goal": "general",
        "summary": "Three full-body sessions; great when you train 2–3×/week.",
        "why": "Low frequency means each muscle should be hit every session to reach weekly volume.",
        "days": [
            {"name": "Full Body A", "items": [_it("Back Squat", 3, "5-8"), _it("Barbell Bench Press", 3, "6-10"),
                _it("Barbell Row", 3, "8-12"), _it("Overhead Press", 3, "8-12"), _it("Leg Curl", 3, "10-15"), _it("Plank", 3, "30-60s")]},
            {"name": "Full Body B", "items": [_it("Deadlift", 3, "4-6"), _it("Incline Dumbbell Press", 3, "8-12"),
                _it("Lat Pulldown", 3, "8-12"), _it("Lateral Raise", 3, "12-20"), _it("Leg Extension", 3, "10-15"), _it("Hanging Leg Raise", 3, "8-15")]},
            {"name": "Full Body C", "items": [_it("Leg Press", 3, "8-12"), _it("Dumbbell Bench Press", 3, "8-12"),
                _it("Seated Cable Row", 3, "8-12"), _it("Dumbbell Curl", 3, "10-15"), _it("Triceps Pushdown", 3, "10-15"), _it("Standing Calf Raise", 3, "10-15")]},
        ],
    },
    {
        "id": "upperlower", "name": "Upper / Lower (4-day)", "days_per_week": 4, "goal": "hypertrophy",
        "summary": "Two upper and two lower days; the classic 4-day fit.",
        "why": "Hits each muscle ~2×/week with enough volume per session and easy recovery.",
        "days": [
            {"name": "Upper A", "items": [_it("Barbell Bench Press", 4, "5-8"), _it("Barbell Row", 4, "6-10"),
                _it("Overhead Press", 3, "8-12"), _it("Lat Pulldown", 3, "8-12"), _it("Lateral Raise", 3, "12-20"), _it("Triceps Pushdown", 3, "10-15"), _it("Barbell Curl", 3, "10-15")]},
            {"name": "Lower A", "items": [_it("Back Squat", 4, "5-8"), _it("Romanian Deadlift", 3, "8-12"),
                _it("Leg Press", 3, "10-15"), _it("Leg Curl", 3, "10-15"), _it("Standing Calf Raise", 4, "10-15"), _it("Hanging Leg Raise", 3, "8-15")]},
            {"name": "Upper B", "items": [_it("Incline Dumbbell Press", 4, "8-12"), _it("Pull-up", 4, "6-10"),
                _it("Machine Shoulder Press", 3, "8-12"), _it("Seated Cable Row", 3, "10-15"), _it("Cable Lateral Raise", 3, "12-20"), _it("Skull Crusher", 3, "10-15"), _it("Hammer Curl", 3, "10-15")]},
            {"name": "Lower B", "items": [_it("Deadlift", 3, "3-5"), _it("Front Squat", 3, "6-10"),
                _it("Bulgarian Split Squat", 3, "8-12"), _it("Leg Extension", 3, "12-15"), _it("Seated Calf Raise", 4, "12-20"), _it("Cable Crunch", 3, "10-15")]},
        ],
    },
    {
        "id": "ppl", "name": "Push Pull Legs (6-day)", "days_per_week": 6, "goal": "hypertrophy",
        "summary": "Push, pull, legs ×2; high frequency for consistent 5–6×/week trainees.",
        "why": "Splits the body so each session is focused and weekly volume is high.",
        "days": [
            {"name": "Push", "items": [_it("Barbell Bench Press", 4, "5-8"), _it("Overhead Press", 3, "8-12"),
                _it("Incline Dumbbell Press", 3, "8-12"), _it("Lateral Raise", 4, "12-20"), _it("Triceps Pushdown", 3, "10-15"), _it("Overhead Triceps Extension", 3, "10-15")]},
            {"name": "Pull", "items": [_it("Deadlift", 3, "4-6"), _it("Pull-up", 4, "6-10"),
                _it("Barbell Row", 3, "8-12"), _it("Face Pull", 4, "12-20"), _it("Barbell Curl", 3, "10-15"), _it("Hammer Curl", 3, "10-15")]},
            {"name": "Legs", "items": [_it("Back Squat", 4, "5-8"), _it("Romanian Deadlift", 3, "8-12"),
                _it("Leg Press", 3, "10-15"), _it("Leg Curl", 3, "10-15"), _it("Standing Calf Raise", 4, "10-15"), _it("Hanging Leg Raise", 3, "8-15")]},
        ],
    },
]


def recommend_split(days, goal="hypertrophy"):
    days = max(1, min(7, int(days or 3)))
    if days <= 3:
        pid, name = "fullbody", "Full Body"
    elif days == 4:
        pid, name = "upperlower", "Upper / Lower"
    else:
        pid, name = "ppl", "Push Pull Legs"
    why = {
        "fullbody": "At 2–3 days a week, full-body sessions let you train each muscle every workout, "
                    "which research suggests matters more than the specific split for reaching weekly volume.",
        "upperlower": "Four days a week splits cleanly into two upper and two lower sessions — each muscle "
                      "trained about twice a week with plenty of recovery.",
        "ppl": "Training 5–6 days a week, Push/Pull/Legs keeps each session focused and lets weekly volume "
               "climb without marathon workouts.",
    }[pid]
    if goal == "strength":
        why += " For a strength goal, bias the first lift toward heavy 3–6 rep work."
    elif goal == "fat_loss":
        why += " For fat loss, keep the lifting heavy to retain muscle and let the deficit drive the rest."
    return {"days": days, "goal": goal, "program_id": pid, "name": name, "why": why}


# ---------------------------------------------------------------- analytics
def epley_1rm(weight, reps):
    if not weight or not reps or reps <= 0:
        return 0.0
    if reps == 1:
        return float(weight)
    return round(float(weight) * (1 + reps / 30.0), 1)


def _muscle_map(conn):
    rows = conn.execute("SELECT id, name, primary_m, secondary_m FROM gym_exercises").fetchall()
    out = {}
    for r in rows:
        out[r["id"]] = (json.loads(r["primary_m"] or "[]"), json.loads(r["secondary_m"] or "[]"), r["name"])
    return out


def _working_sets_since(conn, start_day):
    """All completed working sets from finished/active sessions since start_day.
    Returns rows with exercise_id, day, weight, reps."""
    return conn.execute(
        "SELECT se.exercise_id AS exercise_id, substr(s.started_at,1,10) AS day, "
        "       g.weight AS weight, g.reps AS reps "
        "FROM gym_sets g "
        "JOIN gym_session_exercises se ON se.id=g.se_id "
        "JOIN gym_sessions s ON s.id=se.session_id "
        "WHERE g.done=1 AND g.set_type != 'warmup' AND substr(s.started_at,1,10) >= ?",
        (start_day,),
    ).fetchall()


def muscle_volume(conn, days=7, today=None):
    """Weekly (default 7-day) working-set volume per muscle, with target status
    and days-since-trained (recovery proxy)."""
    today = date.fromisoformat(today) if today else date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    mmap = _muscle_map(conn)
    rows = _working_sets_since(conn, start)

    vol = {m: 0.0 for m in MUSCLE_NAMES}
    last_trained = {}
    for r in rows:
        prim, sec, _ = mmap.get(r["exercise_id"], ([], [], ""))
        for m in prim:
            if m in vol:
                vol[m] += 1.0
                last_trained[m] = max(last_trained.get(m, ""), r["day"])
        for m in sec:
            if m in vol:
                vol[m] += 0.5
                last_trained[m] = max(last_trained.get(m, ""), r["day"])

    out = []
    for m in MUSCLE_NAMES:
        lo, hi = MUSCLES[m]
        sets = round(vol[m], 1)
        if sets < lo * 0.5:
            status = "very low"
        elif sets < lo:
            status = "low"
        elif sets <= hi:
            status = "optimal"
        else:
            status = "high"
        lt = last_trained.get(m)
        dsince = (today - date.fromisoformat(lt)).days if lt else None
        out.append({"muscle": m, "sets": sets, "low": lo, "high": hi,
                    "status": status, "days_since": dsince})
    return {"days": days, "start": start, "end": today.isoformat(), "muscles": out}


def exercise_progression(conn, exercise_id):
    """Per-session best e1RM + heaviest set for one exercise, plus PRs."""
    rows = conn.execute(
        "SELECT s.id AS sid, substr(s.started_at,1,10) AS day, g.weight AS weight, g.reps AS reps "
        "FROM gym_sets g JOIN gym_session_exercises se ON se.id=g.se_id "
        "JOIN gym_sessions s ON s.id=se.session_id "
        "WHERE se.exercise_id=? AND g.done=1 AND g.set_type!='warmup' AND g.weight IS NOT NULL AND g.reps IS NOT NULL "
        "ORDER BY s.started_at",
        (exercise_id,),
    ).fetchall()
    by_session = {}
    for r in rows:
        e = epley_1rm(r["weight"], r["reps"])
        cur = by_session.get(r["sid"])
        if not cur or e > cur["e1rm"]:
            by_session[r["sid"]] = {"day": r["day"], "e1rm": e, "weight": r["weight"], "reps": r["reps"]}
    series = sorted(by_session.values(), key=lambda x: x["day"])
    best_e1rm = max((x["e1rm"] for x in series), default=0)
    best_weight = max((r["weight"] for r in rows), default=0)
    best_reps = max((r["reps"] for r in rows), default=0)
    return {"series": series, "best_e1rm": best_e1rm,
            "best_weight": best_weight, "best_reps": best_reps}


def personal_records(conn, limit=12):
    """Top exercises by estimated 1RM, with the set that produced it."""
    rows = conn.execute(
        "SELECT se.exercise_id AS eid, e.name AS name, g.weight AS weight, g.reps AS reps, "
        "       substr(s.started_at,1,10) AS day "
        "FROM gym_sets g JOIN gym_session_exercises se ON se.id=g.se_id "
        "JOIN gym_sessions s ON s.id=se.session_id JOIN gym_exercises e ON e.id=se.exercise_id "
        "WHERE g.done=1 AND g.set_type!='warmup' AND g.weight IS NOT NULL AND g.reps IS NOT NULL",
    ).fetchall()
    best = {}
    for r in rows:
        e = epley_1rm(r["weight"], r["reps"])
        cur = best.get(r["eid"])
        if not cur or e > cur["e1rm"]:
            best[r["eid"]] = {"exercise": r["name"], "e1rm": e, "weight": r["weight"],
                              "reps": r["reps"], "day": r["day"]}
    out = sorted(best.values(), key=lambda x: -x["e1rm"])
    return out[:limit]


def _parse_ts(s):
    if not s:
        return None
    s = s.strip()
    if not (s.endswith("Z") or s[-6:].count(":") == 1 and (s[-6] in "+-")):
        s = s + "+00:00"
    elif s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def session_duration(started_at, ended_at):
    """Minutes between start and end, or None if unfinished/unparseable."""
    a, b = _parse_ts(started_at), _parse_ts(ended_at)
    if not a or not b:
        return None
    return max(0, round((b - a).total_seconds() / 60))


def calendar(conn, days=90, today=None):
    """Workout days over the last N days: {day: {sessions, volume}}."""
    today = date.fromisoformat(today) if today else date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        "SELECT substr(s.started_at,1,10) AS day, COUNT(DISTINCT s.id) AS sessions, "
        "       COALESCE(SUM(CASE WHEN g.done=1 AND g.set_type!='warmup' THEN g.weight*g.reps ELSE 0 END),0) AS volume "
        "FROM gym_sessions s "
        "LEFT JOIN gym_session_exercises se ON se.session_id=s.id "
        "LEFT JOIN gym_sets g ON g.se_id=se.id "
        "WHERE s.ended_at IS NOT NULL AND substr(s.started_at,1,10) >= ? "
        "GROUP BY substr(s.started_at,1,10)",
        (start,),
    ).fetchall()
    sessions_by_day = {}
    for s in conn.execute(
            "SELECT id, name, started_at, ended_at, substr(started_at,1,10) AS day FROM gym_sessions "
            "WHERE ended_at IS NOT NULL AND substr(started_at,1,10) >= ? ORDER BY started_at", (start,)).fetchall():
        sessions_by_day.setdefault(s["day"], []).append(
            {"id": s["id"], "name": s["name"], "duration": session_duration(s["started_at"], s["ended_at"])})
    return {"start": start, "end": today.isoformat(),
            "days": {r["day"]: {"sessions": r["sessions"], "volume": round(r["volume"] or 0)} for r in rows},
            "sessions": sessions_by_day}


def trends(conn, weeks=8, today=None):
    """Per-week session count + total volume, and recent session durations."""
    today = date.fromisoformat(today) if today else date.today()
    # align to the Monday of the current week
    monday = today - timedelta(days=today.weekday())
    weekly = []
    for i in range(weeks - 1, -1, -1):
        ws = monday - timedelta(days=7 * i)
        we = ws + timedelta(days=6)
        row = conn.execute(
            "SELECT COUNT(DISTINCT s.id) AS sessions, "
            "       COALESCE(SUM(CASE WHEN g.done=1 AND g.set_type!='warmup' THEN g.weight*g.reps ELSE 0 END),0) AS volume "
            "FROM gym_sessions s LEFT JOIN gym_session_exercises se ON se.session_id=s.id "
            "LEFT JOIN gym_sets g ON g.se_id=se.id "
            "WHERE s.ended_at IS NOT NULL AND substr(s.started_at,1,10) BETWEEN ? AND ?",
            (ws.isoformat(), we.isoformat()),
        ).fetchone()
        weekly.append({"week": ws.isoformat(), "sessions": row["sessions"], "volume": round(row["volume"] or 0)})
    durations = []
    for s in conn.execute("SELECT started_at, ended_at FROM gym_sessions WHERE ended_at IS NOT NULL ORDER BY started_at DESC LIMIT 20").fetchall():
        d = session_duration(s["started_at"], s["ended_at"])
        if d is not None:
            durations.append({"day": s["started_at"][:10], "value": d})
    durations.reverse()
    return {"weekly": weekly, "durations": durations}


def goal_current(conn, kind, exercise_id, stored_current, today=None):
    """Live value for a goal, by kind."""
    today = date.fromisoformat(today) if today else date.today()
    if kind == "lift" and exercise_id:
        return exercise_progression(conn, exercise_id)["best_e1rm"]
    if kind == "volume":
        wk = (today - timedelta(days=today.weekday())).isoformat()
        r = conn.execute(
            "SELECT COALESCE(SUM(g.weight*g.reps),0) AS v FROM gym_sets g "
            "JOIN gym_session_exercises se ON se.id=g.se_id JOIN gym_sessions s ON s.id=se.session_id "
            "WHERE g.done=1 AND g.set_type!='warmup' AND substr(s.started_at,1,10) >= ?", (wk,)).fetchone()
        return round(r["v"] or 0)
    if kind == "frequency":
        wk = (today - timedelta(days=today.weekday())).isoformat()
        return conn.execute(
            "SELECT COUNT(*) FROM gym_sessions WHERE ended_at IS NOT NULL AND substr(started_at,1,10) >= ?", (wk,)).fetchone()[0]
    if kind == "bodyweight":
        return latest_bodyweight(conn) or (stored_current or 0)
    return stored_current or 0


def goals(conn, today=None):
    out = []
    for g in conn.execute("SELECT * FROM gym_goals ORDER BY achieved_at IS NOT NULL, id DESC").fetchall():
        cur = goal_current(conn, g["kind"], g["exercise_id"], g["current"], today)
        start = g["start_value"] or 0
        target = g["target"] or 0
        if g["kind"] == "bodyweight" and start and target and start != target:
            # directional: progress from the starting weight toward the target
            pct = max(0, min(100, round(((start - cur) / (start - target)) * 100)))
            achieved = (cur <= target) if target < start else (cur >= target)
        else:
            # absolute (current vs target); start_value kept for the "+gain" label
            pct = max(0, min(100, round((cur / target) * 100))) if target else 0
            achieved = cur >= target and target > 0
        name = g["name"]
        if g["kind"] == "lift" and g["exercise_id"]:
            ex = conn.execute("SELECT name FROM gym_exercises WHERE id=?", (g["exercise_id"],)).fetchone()
            if ex and not name:
                name = ex["name"] + " 1RM"
        out.append({"id": g["id"], "name": name, "kind": g["kind"], "exercise_id": g["exercise_id"],
                    "target": target, "unit": g["unit"], "start_value": start, "current": cur,
                    "gain": round(cur - start, 1), "percent": pct,
                    "achieved": achieved, "achieved_at": g["achieved_at"]})
    return out


def last_performance(conn, exercise_id, exclude_session_id=None):
    """The most recent prior session's working sets for an exercise (for the
    'previous' ghost hint shown while logging)."""
    row = conn.execute(
        "SELECT se.id AS seid, substr(s.started_at,1,10) AS day FROM gym_session_exercises se "
        "JOIN gym_sessions s ON s.id=se.session_id "
        "WHERE se.exercise_id=? AND s.id!=? AND EXISTS(SELECT 1 FROM gym_sets g WHERE g.se_id=se.id AND g.done=1 AND g.set_type!='warmup') "
        "ORDER BY s.started_at DESC LIMIT 1",
        (exercise_id, exclude_session_id or -1)).fetchone()
    if not row:
        return None
    sets = conn.execute(
        "SELECT weight, reps FROM gym_sets WHERE se_id=? AND done=1 AND set_type!='warmup' ORDER BY set_no",
        (row["seid"],)).fetchall()
    return {"day": row["day"], "sets": [{"weight": s["weight"], "reps": s["reps"]} for s in sets]}


def prior_best(conn, exercise_id, exclude_session_id):
    """Best e1RM and heaviest weight for an exercise from sessions OTHER than the
    given one (used to flag a live set as a personal record)."""
    rows = conn.execute(
        "SELECT g.weight AS w, g.reps AS r FROM gym_sets g JOIN gym_session_exercises se ON se.id=g.se_id "
        "WHERE se.exercise_id=? AND se.session_id!=? AND g.done=1 AND g.set_type!='warmup' "
        "AND g.weight IS NOT NULL AND g.reps IS NOT NULL",
        (exercise_id, exclude_session_id or -1)).fetchall()
    return {"e1rm": max((epley_1rm(r["w"], r["r"]) for r in rows), default=0),
            "weight": max((r["w"] for r in rows), default=0)}


def latest_bodyweight(conn):
    r = conn.execute("SELECT value FROM gym_metrics WHERE metric='bodyweight' ORDER BY day DESC, id DESC LIMIT 1").fetchone()
    return r["value"] if r else 0


def metrics(conn):
    """Body metrics grouped by metric name, each with its latest value + series."""
    rows = conn.execute("SELECT metric, day, value, unit FROM gym_metrics ORDER BY metric, day, id").fetchall()
    by = {}
    for r in rows:
        g = by.setdefault(r["metric"], {"unit": r["unit"], "series": []})
        g["series"].append({"day": r["day"], "value": r["value"]})
        if r["unit"]:
            g["unit"] = r["unit"]
    out = []
    for m, g in by.items():
        s = g["series"]
        out.append({"metric": m, "unit": g["unit"], "latest": s[-1]["value"], "first": s[0]["value"],
                    "change": round(s[-1]["value"] - s[0]["value"], 2), "series": s})
    out.sort(key=lambda x: (x["metric"] != "bodyweight", x["metric"]))
    return out


def pr_timeline(conn, limit=50):
    """Chronological personal-record events: each time an exercise's best e1RM
    increased, ordered newest first."""
    rows = conn.execute(
        "SELECT se.exercise_id AS eid, e.name AS name, substr(s.started_at,1,10) AS day, g.weight AS w, g.reps AS r "
        "FROM gym_sets g JOIN gym_session_exercises se ON se.id=g.se_id JOIN gym_sessions s ON s.id=se.session_id "
        "JOIN gym_exercises e ON e.id=se.exercise_id "
        "WHERE g.done=1 AND g.set_type!='warmup' AND g.weight IS NOT NULL AND g.reps IS NOT NULL "
        "ORDER BY s.started_at",
    ).fetchall()
    best, events = {}, []
    for r in rows:
        e = epley_1rm(r["w"], r["r"])
        if e > best.get(r["eid"], 0) + 0.05:
            best[r["eid"]] = e
            events.append({"day": r["day"], "exercise": r["name"], "e1rm": e, "weight": r["w"], "reps": r["r"]})
    events.reverse()
    return events[:limit]


def insights(conn, today=None):
    """Niche training readouts: most-substituted exercises, weakest movement
    pattern, most-skipped routine exercises, and overtraining watch."""
    today = date.fromisoformat(today) if today else date.today()
    most_sub = []
    for r in conn.execute("SELECT from_id, COUNT(*) AS n FROM gym_swaps GROUP BY from_id ORDER BY n DESC LIMIT 5").fetchall():
        ex = conn.execute("SELECT name FROM gym_exercises WHERE id=?", (r["from_id"],)).fetchone()
        if ex:
            most_sub.append({"exercise": ex["name"], "count": r["n"]})

    start = (today - timedelta(days=6)).isoformat()
    patvol = {"push": 0, "pull": 0, "legs": 0}
    for r in conn.execute(
            "SELECT e.pattern AS pat, COUNT(*) AS n FROM gym_sets g JOIN gym_session_exercises se ON se.id=g.se_id "
            "JOIN gym_sessions s ON s.id=se.session_id JOIN gym_exercises e ON e.id=se.exercise_id "
            "WHERE g.done=1 AND g.set_type!='warmup' AND substr(s.started_at,1,10)>=? GROUP BY e.pattern", (start,)).fetchall():
        if r["pat"] in patvol:
            patvol[r["pat"]] = r["n"]
    weakest = None
    if any(patvol.values()):
        wk = min(patvol, key=lambda k: patvol[k])
        weakest = {"pattern": wk, "sets": patvol[wk], "breakdown": patvol}

    cutoff = (today - timedelta(days=27)).isoformat()
    skipped = []
    for r in conn.execute("SELECT DISTINCT exercise_id FROM gym_template_items").fetchall():
        n = conn.execute(
            "SELECT COUNT(*) FROM gym_sets g JOIN gym_session_exercises se ON se.id=g.se_id "
            "JOIN gym_sessions s ON s.id=se.session_id WHERE se.exercise_id=? AND g.done=1 AND substr(s.started_at,1,10)>=?",
            (r["exercise_id"], cutoff)).fetchone()[0]
        if n == 0:
            ex = conn.execute("SELECT name FROM gym_exercises WHERE id=?", (r["exercise_id"],)).fetchone()
            if ex:
                skipped.append(ex["name"])

    over = [{"muscle": m["muscle"], "detail": f"{m['sets']} sets vs {m['low']}–{m['high']} target"}
            for m in muscle_volume(conn, days=7, today=today.isoformat())["muscles"] if m["status"] == "high"]
    return {"most_substituted": most_sub, "weakest_pattern": weakest, "most_skipped": skipped[:8], "overtraining": over}


def recommendations(conn, today=None):
    """Actionable nudges derived from recent training history."""
    today = date.fromisoformat(today) if today else date.today()
    recs = []
    mv = muscle_volume(conn, days=7, today=today.isoformat())["muscles"]

    # neglected / under-trained muscles
    for m in mv:
        if m["days_since"] is not None and m["days_since"] >= 7:
            recs.append({"kind": "neglect", "text": f"You haven't trained {m['muscle']} in {m['days_since']} days."})
    for m in mv:
        if m["status"] in ("very low", "low") and m["sets"] > 0:
            recs.append({"kind": "volume", "text": f"{m['muscle']} is at {m['sets']} weekly sets — below the {m['low']}–{m['high']} target."})

    # e1RM progress this month, per exercise
    month_ago = (today - timedelta(days=30)).isoformat()
    ex_rows = conn.execute("SELECT id, name FROM gym_exercises").fetchall()
    for ex in ex_rows:
        prog = exercise_progression(conn, ex["id"])
        s = prog["series"]
        if len(s) < 2:
            continue
        recent = [x for x in s if x["day"] >= month_ago]
        older = [x for x in s if x["day"] < month_ago]
        if recent and older:
            gain = round(max(x["e1rm"] for x in recent) - max(x["e1rm"] for x in older), 1)
            if gain >= 2:
                recs.append({"kind": "progress", "text": f"Estimated {ex['name']} 1RM is up {gain}kg over the last month. 💪"})

    # high-RIR cue: leaving too much in the tank
    rir_row = conn.execute(
        "SELECT AVG(g.rir) AS avg_rir, COUNT(*) AS n FROM gym_sets g "
        "JOIN gym_session_exercises se ON se.id=g.se_id JOIN gym_sessions s ON s.id=se.session_id "
        "WHERE g.done=1 AND g.set_type='working' AND g.rir IS NOT NULL AND substr(s.started_at,1,10) >= ?",
        ((today - timedelta(days=14)).isoformat(),),
    ).fetchone()
    if rir_row and rir_row["n"] and rir_row["n"] >= 5 and rir_row["avg_rir"] is not None and rir_row["avg_rir"] >= 3:
        recs.append({"kind": "intensity",
                     "text": f"You're averaging {round(rir_row['avg_rir'],1)} reps in reserve — consider adding weight to push closer to failure."})

    if not recs:
        recs.append({"kind": "ok", "text": "Nothing flagged — volume and recovery look balanced. Keep stacking sessions."})
    return recs
