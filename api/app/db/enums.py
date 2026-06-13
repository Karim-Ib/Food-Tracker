import enum



class FoodSource(str, enum.Enum):
    SYSTEM = "system"
    OPENFOODFACTS = "openFoodFacts"
    USER = "user"
    AI_ESTIMATED = "ai_estimated"

class Visibility(str, enum.Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class EntrySource(str, enum.Enum):
    FOOD = "food"
    RECIPE = "recipe"

