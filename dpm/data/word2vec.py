import string
import torch
from torch.utils.data import Dataset, DataLoader
from torch.nn.utils.rnn import pad_sequence
import torchvision.transforms as transforms
from PIL import Image
import json
import os
import numpy as np
from dpm.utils import get_vocabulary

class ImageTextDataset(Dataset):
    def __init__(self, json_file_path, image_folder_path,image_size, vocabulary_path='./data/vocabulary.json'):
        """
        Args:
            json_file_path (string): JSON文件的路径，包含图片名称和对应的描述。
            image_folder_path (string): 包含图片的文件夹路径。
            max_seq_length (int): 最大序列长度。
        """
        with open(json_file_path, 'r', encoding='utf-8') as file:
            self.descriptions = json.load(file)
        self.image_folder_path = image_folder_path
        self.image_size=image_size
        self.vocab=get_vocabulary(vocabulary_path)
    def preprocess_text(self,text):
            """
            对文本进行预处理，将句号与单词分离。
            """
            # 使用空格替换句号，确  保句号被视为独立的单词
            text = text.replace('.', ' . ')
            return text.lower().translate(str.maketrans('', '', string.punctuation.replace('.', '')))
        
    def text_to_indices(self, text):
        preprocessed_text = self.preprocess_text(text)
        words = preprocessed_text.split()
        indices = [self.vocab['<sos>']] + \
                  [self.vocab.get(word, self.vocab['<unk>']) for word in words] + \
                  [self.vocab['<eos>']]
        return indices

    def __len__(self):
        return len(self.descriptions)

    def __getitem__(self, idx):
        image_name, description = list(self.descriptions.items())[idx]
        image_path = os.path.join(self.image_folder_path, image_name)

        # 使用torchvision进行图像处理
        transform = transforms.Compose([
            transforms.Resize(tuple(self.image_size)),
            transforms.ToTensor(),
        ])

        image = Image.open(image_path)
        image = transform(image)
        
        # 如果图像有Alpha通道，只取RGB通道
        if image.shape[0] == 4:
            image = image[:3, :, :]

        indices = self.text_to_indices(description)
        indices_tensor = torch.tensor(indices, dtype=torch.long)
        # 对description应用预处理，并分词
        preprocessed_description = [self.preprocess_text(description)]

        return {'image': image, 'indices': indices_tensor, 'description': preprocessed_description}




class datapreprocess():
    def __init__(self):
        return None
    def pad(self,batch):
        images = [item['image'] for item in batch]
        indices = [item['indices'] for item in batch]
        descriptions = [item['description'] for item in batch]

        # 对索引进行填充
        indices_padded = pad_sequence(indices, batch_first=True, padding_value=0)

        
        # 对描述进行填充

        images_tensor = torch.stack(images)
        

        return {
            'image': images_tensor,
            'indices': indices_padded,
            'description': descriptions
        }

