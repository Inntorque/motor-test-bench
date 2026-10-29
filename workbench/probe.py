"""Read-only MBP compatibility check. The application must release USB-CAN first."""
import argparse
import json
from pathlib import Path
import time
from .devices.motor import MotorDevice,_json_objects

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',type=int,default=1);parser.add_argument('--frozen',type=Path,help='export stopped maximum trace to this directory')
    args=parser.parse_args()
    if not 1<=args.node<=63:parser.error('node must be 1..63')
    motor=MotorDevice();motor.node=args.node
    try:
        motor.connect();time.sleep(.1)
        print(json.dumps(motor.identify(),ensure_ascii=False,indent=2))
        print(json.dumps(motor.latest_snapshot(),ensure_ascii=False,indent=2))
        if args.frozen:
            state=motor.read_state()
            if state.get('flags',1)&1:raise ValueError('输出仍开启，拒绝读取冻结轨迹')
            descriptor,data=motor.read_frozen();args.frozen.mkdir(parents=True,exist_ok=False)
            (args.frozen/'trace.bin').write_bytes(data)
            (args.frozen/'descriptor.json').write_text(json.dumps(_json_objects(descriptor),indent=2)+'\n')
    finally:motor.close()

if __name__=='__main__':main()
