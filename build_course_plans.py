"""Build all configured semester plans and reviewed concept lessons from Canvas exports."""
import argparse
from pathlib import Path
from course_plans.sources import ROOT
from course_plans.curriculum import build_models
from course_plans.render import render_all

def build(snapshot=None,output=None,data_dir=None):
    plans=build_models(snapshot,data_dir);render_all(plans,output or ROOT/'output');return plans

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path);p.add_argument('--output',type=Path,default=ROOT/'output')
    p.add_argument('--data-dir',type=Path);p.add_argument('--no-ai',action='store_true',help='Compatibility flag; reviewed lessons never call an external model.')
    a=p.parse_args();plans=build(a.snapshot,a.output,a.data_dir)
    print(f'Built {len(plans)} course plans, {sum(len(p["concepts"]) for p in plans)} concept lessons and {sum(len(p["weeks"]) for p in plans)} weekly pages.')
    print(a.output/'course_plans/index.html')
if __name__=='__main__':main()
