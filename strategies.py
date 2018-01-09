import copy
import math
import random
import sys
import time
import sgf_wrapper

import gtp
import numpy as np
from mcts import MCTSNode

import go
import utils

MAX_GAME_DEPTH = int(go.N * go.N * 1.25)
# When to do deterministic move selection.  ~30 moves on a 19x19, ~8 on 9x9
TEMPERATURE_CUTOFF = int((go.N * go.N) / 12)

def time_recommendation(move_num, seconds_per_move=5, time_limit=15*60,
                        decay_factor=0.98):
    '''Given current move number and "desired" seconds per move,
    return how much time should actually be used. To be used specifically
    for CGOS time controls, which are absolute 15 minute time.

    The strategy is to spend the maximum time possible using seconds_per_move,
    and then switch to an exponentially decaying time usage, calibrated so that
    we have enough time for an infinite number of moves.'''

    # divide by two since you only play half the moves in a game.
    player_move_num = move_num / 2

    # sum of geometric series maxes out at endgame_time seconds.
    endgame_time = seconds_per_move / (1 - decay_factor)

    if endgame_time > time_limit:
        # there is so little main time that we're already in "endgame" mode.
        base_time = time_limit * (1 - decay_factor)
        return base_time * decay_factor ** player_move_num

    # leave over endgame_time seconds for the end, and play at seconds_per_move
    # for as long as possible
    core_time = time_limit - endgame_time
    core_moves = core_time / seconds_per_move

    if player_move_num < core_moves:
        return seconds_per_move
    else:
        return seconds_per_move * decay_factor ** (player_move_num - core_moves)



class MCTSPlayerMixin:
    # If 'simulations_per_move' is nonzero, it will perform that many reads before playing.
    # Otherwise, it uses 'seconds_per_move' of wall time'
    def __init__(self, network, seconds_per_move=5, simulations_per_move=0,
                 resign_threshold=-0.90, verbosity=0, two_player_mode=False):
        self.network = network
        self.seconds_per_move = seconds_per_move
        self.simulations_per_move = simulations_per_move
        self.verbosity = verbosity
        self.two_player_mode = two_player_mode
        if two_player_mode:
            self.temp_threshold = -1
        else:
            self.temp_threshold = TEMPERATURE_CUTOFF
        self.qs = []
        self.comments = []
        self.searches_pi = []
        self.root = None
        self.result = 0
        self.resign_threshold = -abs(resign_threshold)
        super().__init__()

    def initialize_game(self):
        self.root = MCTSNode(go.Position())
        self.result = 0
        self.comments = []
        self.searches_pi = []
        self.qs = []

    def suggest_move(self, position):
        ''' Used for playing a single game.
        For parallel play, use initialize_move, select_leaf,
        incorporate_results, and pick_move
        '''
        start = time.time()
        if not self.root:
            self.root = MCTSNode(position)

        if self.simulations_per_move == 0 :
            while time.time() - start < self.seconds_per_move:
                self.tree_search()
        else:
            while self.root.N < self.simulations_per_move:
                self.tree_search()

        if self.verbosity > 0:
            print("%d: Searched %d times in %s seconds\n\n" % (
                self.root.position.n, self.root.N, time.time() - start), file=sys.stderr)

        #print some stats on anything with probability > 1%
        if self.verbosity > 2:
            print(self.root.describe(), file=sys.stderr)
            print('\n\n', file=sys.stderr)
        if self.verbosity > 3:
            print(self.root.position, file=sys.stderr)

        return self.pick_move()

    def play_move(self, coords):
        '''
        Notable side effects:
          - finalizes the probability distribution according to
          this roots visit counts into the class' running tally, `searches`
          - Makes the node associated with this move the root, for future
            `inject_noise` calls.
        '''
        if not self.two_player_mode:
            self.searches_pi.append(
                self.root.children_as_pi(self.root.position.n > self.temp_threshold))
        self.qs.append(self.root.Q) # Save our resulting Q.
        self.comments.append(self.root.describe())
        self.root = self.root.add_child(utils.flatten_coords(coords))
        self.position = self.root.position # for showboard
        del self.root.parent.children
        return True # GTP requires positive result.

    def pick_move(self):
        '''Picks a move to play, based on MCTS readout statistics.

        Highest N is most robust indicator. In the early stage of the game, pick
        a move weighted by visit count; later on, pick the absolute max.'''
        if self.root.position.n > self.temp_threshold:
            fcoord = np.argmax(self.root.child_N)
        else:
            cdf = self.root.child_N.cumsum()
            cdf /= cdf[-1]
            selection = random.random()
            fcoord = cdf.searchsorted(selection)
            assert self.root.child_N[fcoord] != 0
        return utils.unflatten_coords(fcoord)

    def tree_search(self):
        leaf = self.root.select_leaf()
        move_probs, value = self.network.run(leaf.position)
        leaf.incorporate_results(move_probs, value, up_to=self.root)

    def is_done(self):
        '''True if the last two moves were Pass or if the position is at a move
        greater than the max depth.  False otherwise.
        '''
        if self.result != 0: #Someone's twiddled our result bit!
            return True

        if self.root.position.is_game_over():
            return True

        if self.root.position.n >= MAX_GAME_DEPTH:
            return True
        return False

    def show_path_to_root(self, node):
        pos = node.position
        if len(pos.recent) == 0:
            return
        moves = list(map(utils.to_human_coord,
                         [move.move for move in pos.recent[self.root.position.n:]]))
        print("From root: ", " <= ".join(moves), file=sys.stderr, flush=True)

    def should_resign(self):
        '''Returns true if the player resigned.  No further moves should be played'''
        if self.root.Q_perspective < self.resign_threshold: # Force resign
            self.result = self.root.position.to_play * -2 # use 2 & -2 as "+resign"
            if self.verbosity > 1:
                res = "B+" if self.result is 2 else "W+"
                print("%sResign: %.3f" % (res, self.root.Q), file=sys.stderr)
                print(self.root.position, self.root.position.score(), file=sys.stderr)
            return True
        return False

    def make_result_string(self, pos):
        if abs(self.result) == 2:
            res = "B+Resign" if self.result == 2 else "W+Resign"
        else:
            res = pos.result()
        return res

    def to_sgf(self):
        pos = self.root.position
        res = self.make_result_string(pos)
        if self.comments:
            self.comments[0] = ("Resign Threshold: %0.3f\n" % self.resign_threshold) + self.comments[0]
        return sgf_wrapper.make_sgf(pos.recent, res,
                                    white_name=self.network.name or "Unknown",
                                    black_name=self.network.name or "Unknown",
                                    comments=self.comments)

    def to_dataset(self):
        assert len(self.searches_pi) == self.root.position.n
        pwcs = list(go.replay_position(self.root.position))[:-1]
        results = np.ones([len(pwcs)], dtype=np.int8)
        if self.result < 0:
            results *= -1
        return (pwcs, self.searches_pi, results)

    def chat(self, msg_type, sender, text):
        default_response = "Supported commands are 'winrate', 'nextplay', 'fortune', and 'help'."
        if self.root is None or self.root.position.n == 0:
            return "I'm not playing right now.  " + default_response

        if 'winrate' in text.lower():
            wr = (abs(self.root.Q) + 1.0) / 2.0
            color = "Black" if self.root.Q > 0 else "White"
            return  "{:s} {:.2f}%".format(color, wr * 100.0)
        elif 'nextplay' in text.lower():
            return "I'm thinking... " + self.root.most_visited_path()
        elif 'fortune' in text.lower():
            return "You're feeling lucky!"
        elif 'help' in text.lower():
            return "I can't help much with go -- try ladders!  Otherwise: " + default_response
        else:
            return default_response

class CGOSPlayerMixin(MCTSPlayerMixin):
    def suggest_move(self, position):
        self.seconds_per_move = time_recommendation(position.n)
        return super().suggest_move(position)
