# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Set your name
# Copyright © 2023 <your name>

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from typing import Optional, Annotated, Any, Callable


from pydantic import ValidationError, WrapValidator


from synth.base.synapse import Synapse
from synth.simulation_input import SimulationInput

# This is the protocol for the miner and validator interaction.
# It is a simple request-response protocol where the validator sends a request
# to the miner, and the miner responds with a simulation_output response.


def invalid_to_none(v: Any, handler: Callable[[Any], Any]) -> Any:
    try:
        return handler(v)
    except ValidationError:
        return v


class Simulation(Synapse):
    """
    A synth protocol representation which uses Synapse as its base.
    This protocol helps in handling simulation_input request and response communication between
    the miner and the validator.

    Attributes:
    - simulation_input: An integer value representing the input request sent by the validator.
    - simulation_output: An optional integer value which, when filled, represents the response from the miner.
    """

    # Required request input, filled by sending dendrite caller.
    simulation_input: SimulationInput

    # Optional request output, filled by receiving axon.
    simulation_output: Annotated[
        tuple[int | list[int | float], ...] | None,
        WrapValidator(invalid_to_none),
    ] = None

    def deserialize(self) -> Optional[list]:
        """
        Deserialize simulation output. This method retrieves the response from
        the miner in the form of simulation_output, deserializes it and returns it
        as the output of the dendrite.query() call.
        """
        return self.simulation_output
