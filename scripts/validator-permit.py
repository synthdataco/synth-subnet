import bittensor as bt
from substrateinterface import SubstrateInterface  # type: ignore

netuid = 50
subnet = bt.Metagraph(netuid)

try:
    wallet = bt.Wallet(name="validator", hotkey="default")
    my_uid = subnet.hotkeys.index(wallet.hotkey.ss58_address)
    print(f"Validator permit: {subnet.validator_permit[my_uid]}")
except Exception as e:
    print(f"Error retrieving validator permit: {e}")

top_64_stake = sorted(subnet.S)[-64:]
print(
    f"Current requirement for validator permits based on the top 64 stake stands at {min(top_64_stake)} alpha"
)

hotkey = "5Gy7xTzDwXYw4JV1TUUUt28PwUTQ95HBCYFJMwkaDmnbQUJ5"
network = "finney"
sub = bt.Subtensor(network)
mg = sub.metagraph(netuid)
if hotkey not in mg.hotkeys:
    print(f"Hotkey {hotkey} deregistered")
else:
    print(f"Hotkey {hotkey} is registered")

print("fetch SubtensorModule ValidatorPermit")
substrate = SubstrateInterface(url="wss://entrypoint-finney.opentensor.ai:443")
result = substrate.query("SubtensorModule", "ValidatorPermit", [netuid])
for uid, permit in enumerate(result.value):
    if permit:
        print(f"neuron uid {uid} has permit")

print("fetch Metagraph validator_permit")
for uid in range(0, 256):
    if mg.validator_permit[uid]:
        print(f"neuron uid {uid} has permit")

print("uid had vpermit and is serving?")
for uid in range(0, 256):
    if mg.validator_permit[uid] and mg.axons[uid].is_serving:
        print(f"axon uid {uid} is serving")
