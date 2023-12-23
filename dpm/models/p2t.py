from typing import Any
from lightning.pytorch.utilities.types import STEP_OUTPUT
import torch
import lightning.pytorch as pl
from lightning.pytorch.utilities.rank_zero import rank_zero_only
from dpm.utils import instantiate_from_config,get_vocabulary,convert_to_word_lists,join_words
from dpm.modules.search_strategy import beam_search,greedy_search
from dpm.evaluation import compute_meteor_score,compute_rouge_score
from numpy import random
class Pic2TextModel(pl.LightningModule):
    def __init__(self,
                 learning_rate,
                 loss,
                 ecconfig,
                 dcconfig,
                 embed_dim,
                vocabulary_path='./data/vocabulary.json',
                 ckpt_path=None,
                 ignore_keys=[],
                 image_key="image",
                 gt_key='indices',
                 gt_text='description',
                 gen_strategy='beam-search'
                 
                 ):
        super().__init__()
        self.lr=learning_rate
        self.loss=instantiate_from_config(loss)
        self.encoder=instantiate_from_config(ecconfig)
        self.decoder=instantiate_from_config(dcconfig)
        self.vocabulary=get_vocabulary(vocabulary_path)
        
        if ckpt_path is not None:
            self.init_from_ckpt(ckpt_path, ignore_keys=ignore_keys)
        self.image_key=image_key
        self.gt_key=gt_key
        self.text_key=gt_text
        self.strategy=gen_strategy
        self.embed=torch.nn.Embedding(num_embeddings=len(self.vocabulary),embedding_dim=embed_dim,padding_idx=0)
        self.output_layer = torch.nn.Linear(embed_dim,len(self.vocabulary))
        self.automatic_optimization=False
    def init_from_ckpt(self, path, ignore_keys=list()):
        sd = torch.load(path, map_location="cpu")["state_dict"]
        keys = list(sd.keys())
        for k in keys:
            for ik in ignore_keys:
                if k.startswith(ik):
                    print("Deleting key {} from state_dict.".format(k))
                    del sd[k]
        self.load_state_dict(sd, strict=False)
        print(f"Restored from {path}")
    def get_data(self, batch, k):
        x = batch[k]
        if  isinstance(x,torch.Tensor):
            if len(x.shape) == 3:
                return x
            elif len(x.shape) ==4 and x.shape[1]!=3:
                return x.permute(0, 3, 1, 2).to(memory_format=torch.contiguous_format).float()
        return x
    
    def forward(self, input, target):
        src = input
        tgt = self.embed(target)
        hidden = self.encoder(src)# [B,S,E]
        output = self.decoder(tgt, hidden)
        logits = self.output_layer(output)

        return logits
    
    
    
    def compute_loss(self, inputs, gt):
        logits = self(inputs, gt[:, :-1])
        out = logits.reshape(-1, logits.size(-1))
        gt = gt[:, 1:].reshape(-1)
        
        loss = self.loss(out, gt)  # teacher forcing
        return loss
    '''def training_step(self, batch, batch_idx):
        opt=self.optimizers()
        opt.zero_grad()
        inputs = self.get_data(batch, self.image_key)
        gt = self.get_data(batch, self.gt_key)
        logits = self(inputs, gt[:, :-1])
        output = logits.reshape(-1, logits.size(-1))

        target = gt[:, 1:].reshape(-1)
        
        
        loss=self.loss(output,target)# teacher forcing
        #self.manual_backward(loss)
        #opt.step()
        self.log('train/loss', loss, on_step=True, on_epoch=True, prog_bar=True)
        return loss'''
    def training_step(self, batch, batch_idx):
        sampling_prob=self.get_sampling_prob()
        opt=self.optimizers()
        opt.zero_grad()
        inputs = self.get_data(batch, self.image_key)
        gt = self.get_data(batch, self.gt_key)

        decoder_input = gt[:, :1]

        # 初始化损失和计数器
        total_loss = 0
        total_count = 0

        for t in range(1, gt.size(1)):
            logits = self(inputs, decoder_input)
            # 每个时间步都计算损失
            loss_t = self.loss(logits[:, -1], gt[:, t])
            total_loss += loss_t

            # 更新计数器
            total_count += 1

            # Scheduled Sampling
            if random.random() < sampling_prob:
                next_input = logits[:, -1].argmax(dim=1).unsqueeze(1)
            else:
                next_input = gt[:, t].unsqueeze(1)

            decoder_input = torch.cat([decoder_input, next_input], dim=1)

        # 使用总损失和计数器来计算平均损失
        loss = total_loss / total_count
        self.manual_backward(loss)
        opt.step()
        self.log('train/loss', loss, on_step=True, on_epoch=True, prog_bar=True)
        return loss
    def validation_step(self, batch, batch_indx) :
        inputs = self.get_data(batch, self.image_key)
        gt = self.get_data(batch, self.gt_key)
        gt_text=self.get_data(batch,self.text_key)
        loss=self.compute_loss(inputs,gt)
        self.log('val/loss',loss,on_step=True,on_epoch=True,prog_bar=True)
        self.log('val_loss',loss,on_step=True,on_epoch=True,prog_bar=True)
        if self.strategy=="greedy":
            output,_=greedy_search(model=self,X=inputs,predictions=gt.shape[1]-2)
            
            best_sequence=output
            # 计算评价指标
        elif self.strategy=="beam":
            result,_ =beam_search(model=self,X=inputs,predictions=gt.shape[1]-2,beam_width=3,batch_size=20)
            min_loss = float('inf')

            # 遍历所有beam search的结果
            for i in range(result.shape[1]):
                # 提取当前序列
                current_sequence = result[:, i, :]

                # 检查是否为最小损失
                if loss < min_loss:
                    min_loss = loss
                    best_sequence = current_sequence
        else:
            raise RuntimeError('strategy doesnt match!')
        best_sequence=best_sequence.cpu().detach().numpy()
        
        gt_text=convert_to_word_lists(gt_text)
        sentence=self.batch_int_sequence_to_text(best_sequence,gt_text)
        gt_text_str_list= [join_words(word_list) for word_list in gt_text]
        gen_text_str_list=[join_words(word_list) for word_list in sentence]
        
        rouge = compute_rouge_score(gt_text_str_list, gen_text_str_list)
        meteor = compute_meteor_score(gt_text,convert_to_word_lists(gen_text_str_list))
        self.log('val/rouge-l', rouge, on_epoch=True)
        #self.log('val/meteor', meteor, on_epoch=True)
                
    '''
    
    def greedy_search(self,inputs,gt):
        sos_batch = torch.full((inputs.shape[0], 1), 1, dtype=torch.long, device=inputs.device)

        # 初始化解码器的输入为 SOS
        decoder_input = sos_batch
        max_length = gt.size(1)  # 假设我们希望生成长度与 gt 一致的序列

        # 逐步生成序列
        output = []
        eos_reached = False
        for _ in range(max_length):
            decoder_output = self(inputs, decoder_input)
            next_token = decoder_output[:, -1, :].argmax(dim=-1, keepdim=True)
            
            if next_token.item() == 2:  # 假设 2 是 EOS 的索引
                eos_reached = True
            
            if eos_reached:
                next_token.fill_(0)  # 用 PAD (0) 填充

            decoder_input = torch.cat([decoder_input, next_token], dim=-1)
            output.append(next_token)

        output = torch.cat(output, dim=1)
        return output'''
    def configure_optimizers(self):
        lr = self.lr
        opt = torch.optim.Adam(list(self.encoder.parameters())+
                                  list(self.decoder.parameters())+list(self.embed.parameters())+list(self.output_layer.parameters()),
                                  lr=lr, betas=(0.5, 0.9),weight_decay=1e-5)
        return opt
    @rank_zero_only
    def log_image_and_text(self,batch):
        with torch.no_grad():
            image=self.get_data(batch,self.image_key)
            gt_text=self.get_data(batch,self.text_key)
            gt_index=self.get_data(batch,self.gt_key)
            log = dict()
            if self.strategy=='greedy':
                index_output,_=greedy_search(model=self,X=image,predictions=gt_index.shape[1])
            elif self.strategy=='beam':
                index_output,_=beam_search(model=self,X=image,predictions=gt_index.shape[1])
            index_output=index_output.cpu().detach().numpy()
            gt_text=convert_to_word_lists(gt_text)
            text_output=self.batch_int_sequence_to_text(index_output,gt_text)
            log['input_img']=image
            gt_text_str_list= [join_words(word_list) for word_list in gt_text]
            gen_text_str_list=[join_words(word_list) for word_list in text_output]
            log["gt_text"] = gt_text_str_list
            log["gen_text"]=gen_text_str_list

        return log
    
    def batch_int_sequence_to_text(self, batch_int_sequences, ground_truth_batch, special_tokens_indexes=[0, 1, 3]):
    
        index_to_word = {index: word for word, index in self.vocabulary.items()}
        
        sentences = []
        
        for int_sequence, ground_truth in zip(batch_int_sequences, ground_truth_batch):
            # 转换每个整数序列为单词序列并过滤特殊词汇
            words = [index_to_word.get(index, "") for index in int_sequence if index not in special_tokens_indexes]
            try:
                eos_index=words.index('<eos>')
            # 确保单词数量与ground_truth中的单词数量一致
            except:
                eos_index=len(words)
            
            words = words[:eos_index]  # 截断或保持words列表以匹配单词数量

            # 将单词序列组合为句子
            sentences.append(words)

        return sentences
    def get_sampling_prob(self, max_steps=30000, initial_prob=0.1, max_prob=0.9):
    # 确保 global_step 不超过 max_steps
        global_step=self.global_step

        # 计算当前的采样概率
        sampling_prob = initial_prob + (max_prob - initial_prob) * (global_step / max_steps)

        # 确保采样概率不超过最大值
        sampling_prob = min(sampling_prob, max_prob)

        return sampling_prob

        

            


