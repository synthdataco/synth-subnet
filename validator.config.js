module.exports = {
  apps: [
    {
      name: "validator cycle low",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--netuid 50 --logging.debug --wallet.name validator --wallet.hotkey default --neuron.nprocs 8 --validator.cycle_name low_frequency",
      env: {
        PYTHONPATH: ".",
      },
    },
    {
      name: "validator cycle high",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--netuid 50 --logging.debug --wallet.name validator --wallet.hotkey default --neuron.nprocs 8 --validator.cycle_name high_frequency",
      env: {
        PYTHONPATH: ".",
      },
    },
    {
      name: "validator cycle scoring",
      interpreter: "python3",
      script: "./neurons/validator.py",
      args: "--netuid 50 --logging.debug --wallet.name validator --wallet.hotkey default --neuron.nprocs 8 --validator.mode light --retention.low.days 11 --retention.high.days 6 --validator.cycle_name scoring",
      env: {
        PYTHONPATH: ".",
      },
    },
  ],
};
