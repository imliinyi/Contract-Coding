# version: 5
# Implementation of the EffectInterface

class EffectInterface:
    def apply(self, snake: 'Snake') -> None:
        """
        Applies the effect to the given Snake instance.

        Args:
            snake ('Snake'): The snake instance to apply the effect to.
        """
        raise NotImplementedError("Subclasses must implement the 'apply' method.")
