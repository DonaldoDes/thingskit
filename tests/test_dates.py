"""Décodage des champs de date Things (bit-packing constaté par inspection
du schéma SQLite réel, cf. bin/thingskit — pas déduit de la documentation).

Format constaté sur `~/Library/Group Containers/…/main.sqlite` (2026-08-11) :
  - startDate / deadline : entier où
        year  = v >> 16
        month = (v >> 12) & 0xF
        day   = (v >> 7) & 0x1F
    Vérifié sur les 3 témoins d'un projet réel de la base :
    132811008 -> 2026-08-18, 132811392 -> 2026-08-21, etc.
  - reminderTime : entier = (heure*64+minute) << 20. Les minutes occupent 6
    bits (0-63) : aucune quantification, Things accepte les minutes non
    rondes. Mesuré le 2026-08-17 sur 10 valeurs `reminderTime` distinctes
    réellement présentes en base, dont 5 à minutes non rondes.
  - status : 0=incomplete, 2=canceled, 3=completed — vérifié par
    AppleScript `status of to do id …` sur un représentant de chaque valeur.
"""
from __future__ import annotations


def test_decode_things_date_atelier_comm_when(thingskit):
    assert thingskit.decode_things_date(132811008) == "2026-08-18"


def test_decode_things_date_atelier_comm_deadline(thingskit):
    assert thingskit.decode_things_date(132811392) == "2026-08-21"


def test_decode_things_date_atelier_sales_when(thingskit):
    assert thingskit.decode_things_date(132811136) == "2026-08-19"


def test_decode_things_date_atelier_sales_deadline(thingskit):
    assert thingskit.decode_things_date(132811776) == "2026-08-24"


def test_decode_things_date_romain_when(thingskit):
    assert thingskit.decode_things_date(132810496) == "2026-08-14"


def test_decode_things_date_none_is_none(thingskit):
    assert thingskit.decode_things_date(None) is None


def test_decode_alarm_time_nine_am(thingskit):
    assert thingskit.decode_alarm_time(603979776) == "09:00"


def test_decode_alarm_time_none_is_none(thingskit):
    assert thingskit.decode_alarm_time(None) is None


# Table mesurée le 2026-08-17 : les 10 valeurs `reminderTime` distinctes
# réellement présentes en base Things, avec l'heure attendue. Cinq d'entre
# elles portent des minutes non rondes (16:15, 16:29, 16:31, 16:45, 16:59) —
# c'est précisément ce que l'ancien témoin fabriqué (29 << 25, jamais observé
# en base) ne pouvait pas révéler : un témoin construit depuis le modèle
# qu'il valide ne prouve rien.
MEASURED_REMINDER_TIMES = [
    (603979776, "09:00"),
    (671088640, "10:00"),
    (805306368, "12:00"),
    (1089470464, "16:15"),
    (1104150528, "16:29"),
    (1106247680, "16:31"),
    (1120927744, "16:45"),
    (1135607808, "16:59"),
    (1140850688, "17:00"),
    (1409286144, "21:00"),
]


def test_decode_alarm_time_measured_witness_with_non_round_minutes(thingskit):
    assert thingskit.decode_alarm_time(1106247680) == "16:31"


def test_decode_alarm_time_reciprocity_on_measured_table(thingskit):
    for reminder_time, expected in MEASURED_REMINDER_TIMES:
        assert thingskit.decode_alarm_time(reminder_time) == expected


def test_status_label_open(thingskit):
    assert thingskit.STATUS_LABELS[0] == "open"


def test_status_label_canceled(thingskit):
    assert thingskit.STATUS_LABELS[2] == "canceled"


def test_status_label_completed(thingskit):
    assert thingskit.STATUS_LABELS[3] == "completed"
