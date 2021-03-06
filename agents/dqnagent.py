"""
EE782 @ IITB
Course Project

kdbeatbox@gmail.com
"""

import sys
import argparse
import logging
import asyncio
import websockets
import json
from collections import defaultdict
import random

import torch
from torch import nn
import numpy as np
from models import DQN
from config import create_model
from config import EPS_START, DECAY_LEN, EPS_END, SAVE_EVERY

logger = logging.getLogger(__name__)
games = {}


init = True
agent = None
test = False


class DQNAgent:

    def __init__(self, player, episode):

        self.EPSILON = EPS_END + (EPS_START - EPS_END)*(1-(episode/DECAY_LEN))
        self.EPSILON = max(self.EPSILON, EPS_END)
        self.n_states = 9
        self.state = np.zeros(self.n_states, dtype=np.int)
        self.player = player
        self.reward = 0
        self.prev_state = None
        self.dqn = DQN(self.n_states+1, self.n_states)

    def get_feature(self, state):
        return list(state)+[2*(self.player==1)-1]

    def reset(self, player, episode):
        self.episode = episode
        self.EPSILON = EPS_END + (EPS_START - EPS_END)*(1-(episode/DECAY_LEN))
        self.EPSILON = max(self.EPSILON, EPS_END)
        self.reward = 0
        self.state = np.zeros(self.n_states, dtype=np.int)
        self.prev_state = None
        self.player = player
        if (episode + 1) % SAVE_EVERY == 0:
            torch.save(self.dqn.model.state_dict(),
                       f"models/self_play_{episode+1}.pth")

    def process_next_state(self):
        if self.prev_state is None:
            return
        x = self.get_feature(self.prev_state)
        x_ = self.get_feature(self.state)
        self.dqn.memorize(x, self.action, self.reward, x_)
        self.dqn.train()

    def register_action(self, row, column, player):
        if self.player == player:
            self.prev_state = self.state.copy()
        self.state[3*row+column] = {1:1,2:-1}[player]

    def next_action(self):
        free_lines = [i for i in range(len(self.state)) if self.state[i] == 0]
        if len(free_lines) == 0:
            return None
        if np.random.random_sample() > self.EPSILON:
            x = self.get_feature(self.state)
            moves = np.argsort(self.dqn.predict(x))
            idx = len(moves) - 1
            reward = 0
            while moves[idx] not in free_lines:
                reward = -20
                idx -= 1
            movei = moves[idx]
            if reward < 0:
                x = self.get_feature(self.state)
                self.dqn.memorize(x, moves[-1], reward, x)
                self.dqn.train()
        else:
            movei = np.random.choice(free_lines)
        movei = int(movei)
        self.action = movei
        r = movei // 3
        c = movei % 3
        return r, c

    def end_game(self, winner):
        if winner == self.player:
            self.reward += 100
        elif winner == 0:
            self.reward += 0
        else:
            self.reward += -100
        x = self.get_feature(self.prev_state)
        x_ = self.get_feature(self.state)
        self.dqn.memorize(x, self.action, self.reward, x_, done=True)
        self.dqn.train(terminal=True)


device = 'cuda' if torch.cuda.is_available() else 'cpu'


class DQNPlayer:
    def __init__(self, player):
        print(f"Running on device: {device.upper()}")
        self.n_states = 9
        self.state = np.zeros(self.n_states, dtype=np.int)
        self.player = player
        self.model = create_model(self.n_states+1, self.n_states).to(device)
        self.model.load_state_dict(torch.load('models/self_play_32000.pth'))
        self.model.eval()

    def reset(self, player):
        self.state = np.zeros(self.n_states, dtype=np.int)
        self.player = player

    def process_next_state(self):
        pass

    def register_action(self, row, column, player):
        self.state[3*row+column] = {1:1,2:-1}[player]

    def get_feature(self, state):
        feature = np.zeros((3,3,3))
        for i, s in enumerate(state):
            feature[i//3][i%3][s] = 1
        return list(feature.ravel())+[2*(self.player==1)-1]

    def next_action(self):
        free_lines = [i for i in range(len(self.state)) if self.state[i] == 0]
        if len(free_lines) == 0:
            return None
        x = self.get_feature(self.state)
        with torch.no_grad():
            x = torch.Tensor(x).to(device).unsqueeze(0)
            moves = np.argsort(self.model(x)[0].cpu())
        idx = len(moves) - 1
        while moves[idx] not in free_lines:
            idx -= 1
        movei = int(moves[idx])
        r = movei // 3
        c = movei % 3
        return r, c

    def end_game(self, winner):
        pass

# MAIN EVENT LOOP


async def handler(websocket, path):
    global init, agent, test
    logger.info("Start listening")
    # msg = await websocket.recv()
    async for msg in websocket:
        logger.info("< {}".format(msg))
        msg = json.loads(msg)
        answer = None
        if msg["type"] == "start":
            if init:
                if not test:
                    agent = DQNAgent(msg["player"], msg["episode"])
                else:
                    agent = DQNPlayer(msg["player"])
                init = False
            else:
                if not test:
                    agent.reset(msg['player'], msg["episode"])
                else:
                    agent.reset(msg['player'])
            if msg["player"] == 1:
                nm = agent.next_action()
                if nm is None:
                    logger.info("Game over")
                    continue
                r, c = nm
                answer = {
                    'type': 'action',
                    'location': [r, c],
                }
            else:
                answer = None

        elif msg["type"] == "action":
            r, c = msg["location"]
            agent.register_action(r, c, msg["player"])
            if msg["nextplayer"] == agent.player:
                agent.process_next_state()
                nm = agent.next_action()
                if nm is None:
                    logger.info("Game over")
                    continue
                nr, nc = nm
                answer = {
                    'type': 'action',
                    'location': [nr, nc],
                }
            else:
                answer = None

        elif msg["type"] == "end":
            r, c = msg["location"]
            agent.register_action(r, c, msg["player"])
            agent.end_game(msg['winner'])
            answer = None
        else:
            logger.error("Unknown message type:\n{}".format(msg))

        if answer is not None:
            await websocket.send(json.dumps(answer))
            logger.info("> {}".format(answer))
    logger.info("Exit handler")


def start_server(port, test_bool):
    global test
    test = test_bool
    server = websockets.serve(handler, 'localhost', port)
    print("Running on ws://127.0.0.1:{}".format(port))
    print(f"Testing: {test}")
    asyncio.get_event_loop().run_until_complete(server)
    asyncio.get_event_loop().run_forever()


# COMMAND LINE INTERFACE

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Start agent to play XO')
    parser.add_argument('--verbose', '-v', action='count',
                        default=0, help='Verbose output')
    parser.add_argument('--quiet', '-q', action='count',
                        default=1, help='Quiet output')
    parser.add_argument('port', metavar='PORT', type=int,
                        help='Port to use for server')
    parser.add_argument('--test', action='store_true', help='Test mode')
    args = parser.parse_args(argv)
    logger.setLevel(
        max(logging.INFO - 10 * (args.verbose - args.quiet), logging.DEBUG))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    start_server(args.port, args.test)


if __name__ == "__main__":
    sys.exit(main())
