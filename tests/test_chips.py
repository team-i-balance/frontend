"""Cascading chips: layout away from the prompt, kept steps, final choice and drawing."""

import unittest

from agent.overlay.chips import CLOSE_S, Cascade, Menu, Option

SCREEN = (0, 0, 1600, 900)
AREA = (300, 600, 1200, 760)  # the prompt box
WORD = (420, 620, 520, 645)


def tree(log):
    reasons = Menu("사유", [Option("업무상 필요", lambda: log.append("keep")), Option("승인", lambda: log.append("ok"))])
    item = Menu("처리", [Option("가린 채로", lambda: log.append("mask")), Option("원문 유지", menu=reasons)])
    more = Menu("다른 기능", [Option("답변 복원", lambda: log.append("restore"))])
    return Menu("전화번호", [Option("이 항목", menu=item), Option("전체 점검", lambda: log.append("all")),
                         Option("다른 기능", menu=more)], note="010-****-6789")


def center(box):
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


class CascadeTests(unittest.TestCase):
    def make(self, log, screen=SCREEN, area=AREA):
        cascade = Cascade(tree(log), WORD, area, screen, 1.0, now=10.0)
        cascade.step(10.5)
        return cascade

    def test_first_chip_keeps_clear_of_the_prompt_and_screen(self):
        cascade = self.make([])
        card = cascade.cards[0]
        self.assertLessEqual(card.ty + card.h, AREA[1] - 40)  # well above the prompt box
        self.assertGreaterEqual(card.tx, 0)
        line = card.line
        self.assertEqual(line[0][1], WORD[1] - 6)  # starts at the word
        self.assertEqual(line[-1][1], card.ty + card.h)  # ends on the chip
        # No room above: the chip goes below the prompt box instead.
        low = self.make([], area=(300, 40, 1200, 200))
        self.assertGreaterEqual(low.cards[0].ty, 200 + 40)

    def test_steps_stay_and_final_choice_closes_everything_then_runs(self):
        log = []
        cascade = self.make(log)
        cascade.click(*center(cascade.cards[0].row_box(0)))
        cascade.step(11.0)
        self.assertEqual(len(cascade.cards), 2)
        self.assertEqual(cascade.cards[0].selected, 0)
        self.assertGreater(cascade.cards[1].tx, cascade.cards[0].tx + cascade.cards[0].w)
        cascade.click(*center(cascade.cards[1].row_box(1)))
        cascade.step(11.5)
        self.assertEqual([c.selected for c in cascade.cards[:2]], [0, 1])
        self.assertEqual(len(cascade.cards), 3)
        # Choosing another row on an earlier chip replaces only the later chips.
        cascade.click(*center(cascade.cards[0].row_box(2)))
        cascade.step(11.6)
        self.assertEqual([c.menu.title for c in cascade.cards], ["전화번호", "다른 기능"])
        self.assertEqual(len(cascade.leaving), 2)
        cascade.step(12.0)
        self.assertEqual(cascade.leaving, [])
        cascade.click(*center(cascade.cards[1].row_box(0)))
        self.assertFalse(cascade.contains(*center(cascade.cards[0].row_box(0))))
        self.assertIsNone(cascade.step(12.0 + CLOSE_S / 2))
        self.assertEqual(log, [])  # runs only after the chips are gone
        action = cascade.step(12.0 + CLOSE_S + 0.1)
        action()
        self.assertEqual(log, ["restore"])
        self.assertIsNone(cascade.step(13.0))

    def test_close_button_and_render(self):
        log = []
        cascade = self.make(log)
        image, left, top = cascade.render(10.5)
        card = cascade.cards[0]
        self.assertLessEqual(left, card.tx)
        x, y = center(card.row_box(1))
        self.assertEqual(image.getpixel((x - left, y - top))[3], 255)  # opaque chip
        self.assertEqual(image.getpixel((WORD[0] + 10 - left, WORD[1] + 10 - top))[3], 0)  # the word shows through
        cascade.click(*center(card.close_box()))
        action = cascade.step(10.5 + CLOSE_S + 0.1)
        action()
        self.assertEqual(log, [])

    def test_cascade_stays_on_a_narrow_screen(self):
        cascade = self.make([], screen=(0, 0, 900, 900), area=(300, 600, 880, 760))
        cascade.click(*center(cascade.cards[0].row_box(0)))
        cascade.step(11)
        cascade.click(*center(cascade.cards[1].row_box(1)))
        cascade.step(11.5)
        self.assertLessEqual(max(c.tx + c.w for c in cascade.cards), 900)
        self.assertGreaterEqual(min(c.tx for c in cascade.cards), 0)


if __name__ == "__main__":
    unittest.main()
