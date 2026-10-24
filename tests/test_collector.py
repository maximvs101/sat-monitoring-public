import unittest

from monitoring.collector import DeviceLoop


class Clock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


class Recorder:
    def __init__(self):
        self.samples, self.states, self.paused = [], [], 0

    def sample(self, ts, v):
        self.samples.append((ts, v))

    def state(self, ts, state, detail):
        self.states.append(state)

    def on_pause(self):
        self.paused += 1


def failing():
    raise OSError("délai")


def make(read, clock, rec, timer=lambda: 0.0, **kw):
    # clock : horodatages (heure du système) ; timer : durées (horloge monotone), lecture instantanée par défaut.
    return DeviceLoop("fsh", 0.25, read, rec.sample, rec.state, down_after=3, retry_s=5.0,
                      close=rec.on_pause, clock=clock, timer=timer, **kw)


class LoopTest(unittest.TestCase):
    def test_midpoint_timestamp(self):
        rec = Recorder()
        loop = make(lambda: "v", Clock(10.0, 10.2), rec)
        loop.step()
        self.assertEqual(rec.samples, [(10100, "v")])
        self.assertEqual(rec.states, ["up"])
        self.assertEqual(loop.state, "up")

    def test_wait_no_catch_up(self):
        rec = Recorder()
        self.assertEqual(make(lambda: "v", Clock(0.0, 0.3), rec, timer=Clock(0.0, 0.3, 0.3)).step(), 0)
        self.assertAlmostEqual(make(lambda: "v", Clock(0.0, 0.05), rec, timer=Clock(0.0, 0.05, 0.05)).step(), 0.2)

    def test_clock_step_back_does_not_stall_collection(self):
        # L'heure du système recule d'une heure pendant une lecture : l'attente reste celle d'une lecture normale.
        rec = Recorder()
        loop = make(lambda: "v", Clock(7200.0, 3600.05), rec, timer=Clock(100.0, 100.05, 100.05))
        self.assertAlmostEqual(loop.step(), 0.2)

    def test_min_idle_between_reads(self):
        # Le FSH4 a besoin de silence entre deux salves de requêtes pour rafraîchir sa mesure.
        rec = Recorder()
        self.assertAlmostEqual(make(lambda: "v", Clock(0.0, 0.28), rec, timer=Clock(0.0, 0.28, 0.28), min_idle_s=0.2).step(), 0.2)
        self.assertAlmostEqual(make(lambda: "v", Clock(0.0, 0.01), rec, timer=Clock(0.0, 0.01, 0.01), min_idle_s=0.2).step(), 0.24)

    def test_down_after_three_failures(self):
        rec = Recorder()
        loop = make(failing, Clock(*[0.0] * 8), rec)
        waits = [loop.step() for _ in range(4)]
        self.assertEqual(rec.states, ["down"])
        self.assertEqual(waits[2], 5.0)
        self.assertEqual(waits[3], 5.0)
        self.assertEqual(loop.state, "down")

    def test_busy_device_is_not_a_failure(self):
        from monitoring.collector import DeviceBusy
        rec = Recorder()
        def busy():
            raise DeviceBusy("503")
        loop = make(busy, Clock(*[0.0] * 40), rec)
        loop.state = "up"
        for _ in range(10):
            self.assertAlmostEqual(loop.step(), 0.25)
        self.assertEqual(rec.states, [])
        self.assertEqual(rec.samples, [])

    def test_busy_too_long_counts_as_failure(self):
        from monitoring.collector import DeviceBusy, MAX_BUSY
        rec = Recorder()
        def busy():
            raise DeviceBusy("503")
        loop = make(busy, Clock(*[0.0] * 200), rec)
        for _ in range(MAX_BUSY + 3):
            loop.step()
        self.assertEqual(rec.states, ["down"])

    def test_busy_count_reset_on_new_device(self):
        # Un nouvel appareil (ou la reprise après une pause) repart de zéro, comme pour les échecs.
        from monitoring.collector import DeviceBusy, MAX_BUSY
        rec = Recorder()
        def busy():
            raise DeviceBusy("503")
        loop = make(busy, Clock(*[0.0] * 200), rec)
        for _ in range(MAX_BUSY):
            loop.step()
        loop.replace_reader(busy)
        loop.step()
        self.assertEqual(rec.states, ["unknown"])  # occupé, pas en échec
        for _ in range(MAX_BUSY - 1):
            loop.step()
        loop.pause()
        loop.step()
        loop.resume()
        loop.step()
        self.assertNotIn("down", rec.states)

    def test_up_after_down(self):
        rec = Recorder()
        results = [failing, failing, failing, lambda: "v"]
        loop = make(lambda: results.pop(0)(), Clock(*[0.0] * 8), rec)
        for _ in range(4):
            loop.step()
        self.assertEqual(rec.states, ["down", "up"])

    def test_failures_reset_after_success(self):
        rec = Recorder()
        results = [failing, failing, lambda: "v", failing, failing]
        loop = make(lambda: results.pop(0)(), Clock(*[0.0] * 10), rec)
        for _ in range(5):
            loop.step()
        self.assertEqual(rec.states, ["up"])

    def test_pause_applied_by_loop_not_caller(self):
        # La fermeture de la socket se fait dans le thread de collecte, jamais en pleine lecture.
        rec = Recorder()
        reads = []
        loop = make(lambda: reads.append(1) or "v", Clock(*[0.0] * 10), rec)
        loop.pause()
        self.assertEqual(rec.paused, 0)
        loop.step()
        self.assertEqual(rec.paused, 1)
        self.assertEqual(loop.state, "paused")
        self.assertEqual(rec.states, ["paused"])
        self.assertEqual(reads, [])
        loop.step()
        self.assertEqual(rec.paused, 1)
        loop.resume()
        loop.step()
        self.assertEqual(reads, [1])
        self.assertEqual(rec.states, ["paused", "up"])

    def test_processing_time_counts_in_the_period(self):
        # Lecture 0,1 s puis traitement (base, détection) 0,1 s : la lecture suivante part 0,05 s plus tard (0,25 s en
        # tout), sans attendre 0,15 s de plus.
        now = [0.0]

        def read():
            now[0] += 0.1
            return "v"

        class Slow(Recorder):
            def sample(self, ts, v):
                now[0] += 0.1
                super().sample(ts, v)

        rec = Slow()
        loop = make(read, Clock(*[0.0] * 10), rec, timer=lambda: now[0])
        self.assertAlmostEqual(loop.step(), 0.05)

    def test_pause_after_racy_resume_still_closes(self):
        # Reprise survenue entre le test de pause et l'application de la pause (autre fil) : la pause suivante doit
        # quand même fermer la connexion.
        rec = Recorder()
        loop = make(lambda: "v", Clock(*[0.0] * 20), rec)
        loop.pause()
        loop.step()                # pause appliquée
        loop._paused.clear()       # reprise « au mauvais moment » : le drapeau d'application n'est pas remis à zéro
        loop.step()                # lecture
        loop.pause()
        loop.step()
        self.assertEqual(rec.paused, 2)
        self.assertEqual(loop.state, "paused")

    def test_pause_during_read_does_not_report_up(self):
        rec = Recorder()
        loop = make(lambda: loop.pause() or "v", Clock(*[0.0] * 10), rec)
        loop.step()
        self.assertNotIn("up", rec.states)
        loop.step()
        self.assertEqual(rec.paused, 1)
        self.assertEqual(loop.state, "paused")

    def test_pause_wakes_waiting_loop(self):
        import time
        rec = Recorder()
        loop = DeviceLoop("fsh", 0.01, failing, rec.sample, rec.state, 1, retry_s=30.0, close=rec.on_pause)
        loop.start()
        time.sleep(0.1)  # en attente de 30 s après l'échec
        loop.pause()
        time.sleep(0.2)
        loop.stop()
        loop.join(1)
        self.assertEqual(rec.paused, 1)
        self.assertFalse(loop.is_alive())

    def test_replace_reader_applied_by_loop(self):
        rec = Recorder()
        results = [failing, failing, failing]
        loop = make(lambda: results.pop(0)(), Clock(*[0.0] * 20), rec)
        for _ in range(3):
            loop.step()
        self.assertEqual(loop.state, "down")
        closed = []
        loop.replace_reader(lambda: "nouveau", close=lambda: closed.append("nouveau"))
        self.assertEqual(rec.paused, 0)  # rien n'est fait hors du thread de collecte
        loop.step()
        self.assertEqual(rec.paused, 1)  # l'ancienne connexion est fermée
        self.assertEqual(rec.samples[-1][1], "nouveau")
        self.assertEqual(rec.states, ["down", "unknown", "up"])
        loop.pause()
        loop.step()
        self.assertEqual(closed, ["nouveau"])

    def test_replace_reader_while_paused_stays_paused_then_resumes(self):
        # Pause FSH, puis nouvelle adresse dans Appareils… : la boucle reste « en pause », et reprend ensuite.
        rec = Recorder()
        loop = make(lambda: "ancien", Clock(*[0.0] * 20), rec)
        loop.step()
        loop.pause()
        loop.step()
        loop.replace_reader(lambda: "nouveau")
        loop.step()
        self.assertEqual(loop.state, "paused")
        self.assertEqual(rec.states, ["up", "paused", "paused"])
        loop.resume()
        loop.step()
        self.assertEqual((loop.state, rec.samples[-1][1]), ("up", "nouveau"))

    def test_thread_stops(self):
        rec = Recorder()
        import time
        loop = DeviceLoop("fsh", 0.01, lambda: "v", rec.sample, rec.state, 3, 5.0)
        loop.start()
        time.sleep(0.05)
        loop.stop()
        loop.join(1)
        self.assertFalse(loop.is_alive())
        self.assertGreater(len(rec.samples), 0)


if __name__ == "__main__":
    unittest.main()
