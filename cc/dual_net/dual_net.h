// Copyright 2018 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef CC_DUAL_NET_DUAL_NET_H_
#define CC_DUAL_NET_DUAL_NET_H_

#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "absl/synchronization/mutex.h"
#include "absl/types/span.h"
#include "cc/constants.h"
#include "cc/model/model.h"
#include "cc/position.h"
#include "cc/random.h"
#include "cc/symmetries.h"

namespace minigo {

// The input features to the DualNet neural network have 17 binary feature
// planes. 8 feature planes X_t indicate the presence of the current player's
// stones at time t. A further 8 feature planes Y_t indicate the presence of
// the opposing player's stones at time t. The final feature plane C holds all
// 1s if black is to play, or 0s if white is to play. The planes are
// concatenated together to give input features:
//   [X_t, Y_t, X_t-1, Y_t-1, ..., X_t-7, Y_t-7, C].
class DualNet : public Model {
 public:
  // Size of move history in the stone features.
  static constexpr int kMoveHistory = 8;

  // Number of features per stone.
  static constexpr int kNumStoneFeatures = kMoveHistory * 2 + 1;

  // Index of the per-stone feature that describes whether the black or white
  // player is to play next.
  static constexpr int kPlayerFeature = kMoveHistory * 2;

  // Total number of features for the board.
  static constexpr int kNumBoardFeatures = kN * kN * kNumStoneFeatures;

  // TODO(tommadams): Change features type from float to uint8_t.
  using StoneFeatures = std::array<float, kNumStoneFeatures>;
  using BoardFeatures = std::array<float, kNumBoardFeatures>;

  using Input = Model::Input;
  using Output = Model::Output;

  static void SetFeatures(absl::Span<const Position::Stones* const> history,
                          Color to_play, BoardFeatures* features);

  DualNet(std::string name, bool random_symmetry, uint64_t random_seed);
  ~DualNet() override;

  void RunMany(const std::vector<const Input*>& inputs,
               std::vector<Output*>* outputs, std::string* model_name) override;

 private:
  // Runs inference on a batch of input features.
  // TODO(tommadams): rename model -> model_name.
  virtual void RunManyImpl(std::string* model_name) = 0;

 protected:
  std::vector<symmetry::Symmetry> symmetries_used_;
  std::vector<BoardFeatures> features_;
  std::vector<Output> raw_outputs_;

  const bool random_symmetry_;
  Random rnd_;
};

class DualNetFactory : public ModelFactory {
 public:
  // random_symmetry: whether to enable random symmetry in models created by
  //                  this factory.
  // random_seed: seed for random symmetries (each model instance gets a
  //              unique seed from this one). Pass 0 to use a randomly
  //              generated seed, seeded from the platform's entropy source
  //              (e.g. /dev/rand).
  DualNetFactory(bool random_symmetry, uint64_t random_seed);

  bool random_symmetry() const { return random_symmetry_; }

 protected:
  uint64_t GetModelSeed() LOCKS_EXCLUDED(&mutex_);

 private:
  const bool random_symmetry_;

  // TODO(tommadams): switch Random to use pcg32, then we can replace this mutex
  // with an std::atomic<uint32_t> sequence number instead.
  absl::Mutex mutex_;
  Random rnd_ GUARDED_BY(&mutex_);
};

}  // namespace minigo

#endif  // CC_DUAL_NET_DUAL_NET_H_
