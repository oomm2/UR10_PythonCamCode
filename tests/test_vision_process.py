"""A spawned vision process reading generated video, never a physical camera."""
from vision_control.clock import monotonic
import multiprocessing as mp
import os
import tempfile
import time
import unittest
import cv2
import numpy as np
from vision_control.vision import VisionClient

class VisionProcessTests(unittest.TestCase):
    def test_spawn_inference_and_end_of_stream_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'synthetic.avi')
            writer=cv2.VideoWriter(path,cv2.VideoWriter_fourcc(*'MJPG'),30.,(64,64))
            self.assertTrue(writer.isOpened(),'MJPG encoder required for synthetic input test')
            for _ in range(120):writer.write(np.zeros((64,64,3),dtype=np.uint8))
            writer.release()
            state=mp.get_context('spawn').Array('d',2,lock=True)
            client=VisionClient(path,.8,.6,state)
            saw_frame=False
            saw_error=False
            try:
                deadline=monotonic()+20
                while monotonic()<deadline:
                    sample=client.poll()
                    if sample and sample[0]=='frame':
                        saw_frame=True
                        self.assertLessEqual(sample[1],monotonic())
                        self.assertFalse(sample[5])
                    elif sample and sample[0]=='error':
                        saw_error=True
                        break
                    time.sleep(.01)
                self.assertTrue(saw_frame,'Spawned process produced no synthetic frame')
                self.assertTrue(saw_error,'End of stream did not produce an error')
                self.assertEqual(state[0],0)
            finally:
                client.close()
            self.assertFalse(client.process.is_alive())
